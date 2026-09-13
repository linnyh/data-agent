// 数枢桌面壳(DataPivot):sidecar 生命周期管理
// 流程:探测空闲端口(8765 起 +1)→ 首启生成 AppSupport 数据目录与 .env 模板
//   → spawn Python sidecar → 健康检查(15s)→ 窗口导航 → 退出清理/崩溃重启一次
//
// sidecar 为 PyInstaller onedir 目录(Tauri externalBin 机制不支持目录),
// 故放在 bundle Resources(binaries/<name>-<triple>/),std::process 自管理。

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, Url};
use tauri_plugin_log::{Target, TargetKind};

const PORT_START: u16 = 8765;
const PORT_TRIES: u16 = 20;
const READY_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_RESTARTS: u32 = 1;

/// sidecar 配置模板:全注释,用户取消注释填写后重启 App 生效
const ENV_TEMPLATE: &str = r#"# 数枢 DataPivot 配置模板
# 取消注释并填写后重启 App 生效(云 API,OpenAI 兼容协议)
# MODEL_API_URL=
# MODEL_API_KEY=
# MODEL_NAME=
#
# 视频链视觉模型(留空回退上方主模型)
# VIDEO_MODEL_NAME=
# VIDEO_MODEL_API_URL=
# VIDEO_MODEL_API_KEY=
#
# LangSmith 追踪(可选)
# LANGSMITH_TRACING=false
# LANGSMITH_API_KEY=
# LANGSMITH_PROJECT=
"#;

#[derive(Default)]
struct SidecarState {
    /// 当前 sidecar 主进程 pid(bootloader);退出清理按 pid 发信号
    pid: Option<u32>,
    stopping: bool,
    restarts: u32,
}

/// 从 start 起探测空闲端口(bind 成功即空闲,listener 立即释放归还系统)。
/// 与 sidecar 同绑 127.0.0.1(桌面场景仅本机监听):macOS 的通配/特定地址
/// bind 互不冲突,探测语义必须与 sidecar 完全一致才不会误判。
fn find_free_port(start: u16, tries: u16) -> Option<u16> {
    (start..start.saturating_add(tries))
        .find(|p| TcpListener::bind(("127.0.0.1", *p)).is_ok())
}

/// ponytail: 自用 macOS 单平台,直接拼 AppSupport 路径,不做跨平台分支
fn app_support_dir(app: &AppHandle) -> tauri::Result<PathBuf> {
    Ok(app
        .path()
        .home_dir()?
        .join("Library")
        .join("Application Support")
        .join("DataPivot"))
}

/// sidecar 可执行文件路径:
/// - dev: src-tauri/binaries/data-agent-server-<triple>/data-agent-server
/// - prod: .app/Contents/Resources/binaries/data-agent-server-<triple>/data-agent-server
fn sidecar_binary(app: &AppHandle) -> PathBuf {
    let dir_name = format!("data-agent-server-{}", env!("TARGET_TRIPLE"));
    if cfg!(debug_assertions) {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("binaries")
            .join(dir_name)
            .join("data-agent-server")
    } else {
        app.path()
            .resource_dir()
            .expect("bundle 资源目录缺失")
            .join("binaries")
            .join(dir_name)
            .join("data-agent-server")
    }
}

fn sidecar_url(port: u16) -> Result<Url, String> {
    Url::parse(&format!("http://127.0.0.1:{port}")).map_err(|e| e.to_string())
}

/// HTTP 探活(§4.1:GET /sessions 返回 200 且响应体含 "sessions")。
/// 比裸 TCP connect 强:验证端口上真的是本服务,而非抢走端口的陌生进程。
fn sidecar_healthy(port: u16) -> bool {
    let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.write_all(b"GET /sessions HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n");
    let mut buf = Vec::new();
    if stream.read_to_end(&mut buf).is_err() || buf.is_empty() {
        return false;
    }
    let text = String::from_utf8_lossy(&buf);
    text.starts_with("HTTP/1.1 200") && text.contains("\"sessions\"")
}

/// 启动 sidecar 并开线程把 stdout/stderr 转发到日志。
/// process_group(0) 让 sidecar 及其子孙(PyInstaller bootloader → python → solver.py)
/// 归入独立进程组,退出清理时 killpg 一次清干净。
fn spawn_sidecar(binary: &Path, data_dir: &Path, port: u16) -> std::io::Result<Child> {
    let mut cmd = Command::new(binary);
    cmd.env("DATA_AGENT_DATA_DIR", data_dir)
        .env("DATA_AGENT_PORT", port.to_string())
        .env("DATA_AGENT_HOST", "127.0.0.1") // 仅本机监听,与端口探测语义一致
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0);
    let mut child = cmd.spawn()?;

    for (name, stream) in [
        ("out", child.stdout.take().map(|s| Box::new(s) as Box<dyn std::io::Read + Send>)),
        ("err", child.stderr.take().map(|s| Box::new(s) as Box<dyn std::io::Read + Send>)),
    ] {
        if let Some(stream) = stream {
            std::thread::spawn(move || {
                for line in BufReader::new(stream).lines().map_while(Result::ok) {
                    log::info!("[sidecar:{name}] {line}");
                }
            });
        }
    }
    Ok(child)
}

/// 监督任务:启动 → 等就绪 → 导航;进程退出后按策略重启或放弃。
/// 每次 spawn(含崩溃重启)前重新探测端口:上一轮占用可能已变化
/// (如启动失败时端口被别的进程抢走)。
async fn supervise(app: AppHandle, state: Arc<Mutex<SidecarState>>, data_dir: PathBuf) {
    let binary = sidecar_binary(&app);

    loop {
        let port = match find_free_port(PORT_START, PORT_TRIES) {
            Some(p) => p,
            None => {
                log::error!("无空闲端口(8765 起 {PORT_TRIES} 个均被占用),放弃启动");
                return;
            }
        };
        let url = match sidecar_url(port) {
            Ok(u) => u,
            Err(e) => {
                log::error!("构造 sidecar URL 失败: {e}");
                return;
            }
        };
        let mut child = match spawn_sidecar(&binary, &data_dir, port) {
            Ok(c) => c,
            Err(e) => {
                log::error!("sidecar 启动失败(dev 模式需先运行 scripts/build-sidecar.sh): {e}");
                return;
            }
        };
        state.lock().unwrap().pid = Some(child.id());
        log::info!("sidecar 已启动 pid={} port={port}", child.id());

        // 等待就绪(HTTP 探活)或进程早夭
        let deadline = Instant::now() + READY_TIMEOUT;
        let mut ready = false;
        loop {
            if sidecar_healthy(port) {
                ready = true;
                break;
            }
            if let Some(status) = child.try_wait().expect("检查 sidecar 状态失败") {
                log::error!("sidecar 启动即退出: {status}");
                break;
            }
            if Instant::now() > deadline {
                log::error!("sidecar 就绪超时({}s)", READY_TIMEOUT.as_secs());
                break;
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }

        if ready {
            if let Some(w) = app.get_webview_window("main") {
                if let Err(e) = w.navigate(url.clone()) {
                    log::error!("窗口导航失败: {e}");
                }
            }
        }

        // 运行期:阻塞直到进程退出
        let status = child.wait().expect("等待 sidecar 退出失败");
        log::warn!("sidecar 退出: {status}");

        let (stopping, restarts) = {
            let s = state.lock().unwrap();
            (s.stopping, s.restarts)
        };
        if stopping {
            let mut s = state.lock().unwrap();
            s.pid = None;
            log::info!("sidecar 已停止,监督任务结束");
            return;
        }
        if restarts < MAX_RESTARTS {
            state.lock().unwrap().restarts += 1;
            log::warn!("sidecar 异常退出,1s 后重启({}/{})", restarts + 1, MAX_RESTARTS);
            tokio::time::sleep(Duration::from_secs(1)).await;
            continue;
        }
        log::error!("sidecar 连续崩溃,放弃重启");
        state.lock().unwrap().pid = None;
        return;
    }
}

/// App 退出清理:对 sidecar 进程组发 SIGTERM 优雅退出(uvicorn 关停,bootloader
/// 随之退出),2s 后 SIGKILL 兜底清组——保证无残留进程(§4.1)。
fn stop_sidecar(state: &Arc<Mutex<SidecarState>>) {
    let pid = {
        let mut s = state.lock().unwrap();
        s.stopping = true;
        s.pid.take()
    };
    if let Some(pid) = pid {
        log::info!("停止 sidecar 进程组 pgid={pid}");
        unsafe { libc::kill(-(pid as i32), libc::SIGTERM) };
        std::thread::sleep(Duration::from_secs(2));
        unsafe { libc::kill(-(pid as i32), libc::SIGKILL) };
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            // 日志落盘 ~/Library/Logs/com.datapivot.desktop/(§4.1 日志落盘),
            // dev 与 release 一致,便于用户排障
            app.handle().plugin(
                tauri_plugin_log::Builder::default()
                    .targets([
                        Target::new(TargetKind::LogDir {
                            file_name: Some("datapivot".into()),
                        }),
                        Target::new(TargetKind::Stdout),
                    ])
                    .level(log::LevelFilter::Info)
                    .build(),
            )?;
            let data_dir = app_support_dir(app.handle())?;
            std::fs::create_dir_all(&data_dir)?;
            let env_path = data_dir.join(".env");
            if !env_path.exists() {
                std::fs::write(&env_path, ENV_TEMPLATE)?;
            }
            let state = Arc::new(Mutex::new(SidecarState::default()));
            app.manage(state.clone());
            tauri::async_runtime::spawn(supervise(app.handle().clone(), state, data_dir));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if matches!(event, RunEvent::Exit) {
                stop_sidecar(&app.state::<Arc<Mutex<SidecarState>>>().inner().clone());
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn find_free_port_returns_open_port() {
        let port = find_free_port(PORT_START, PORT_TRIES).expect("应能找到空闲端口");
        assert!((PORT_START..PORT_START + PORT_TRIES).contains(&port));
    }

    #[test]
    fn find_free_port_skips_taken_port() {
        // 与 sidecar 同用 127.0.0.1 占位(探测语义必须与 sidecar 绑定一致)
        let taken = TcpListener::bind(("127.0.0.1", 0)).expect("bind 随机端口");
        let taken_port = taken.local_addr().expect("获取地址").port();
        let found = find_free_port(taken_port, 2);
        assert!(found.is_some() && found != Some(taken_port));
    }
}
