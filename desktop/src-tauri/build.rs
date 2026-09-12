fn main() {
  tauri_build::build();
  // sidecar 目录名带 target triple(scripts/build-sidecar.sh 的产物命名)
  println!("cargo:rustc-env=TARGET_TRIPLE={}", std::env::var("TARGET").unwrap());
}
