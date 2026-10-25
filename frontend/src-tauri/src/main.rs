// 内嵌本地执行器的子进程桥：JS 通过 spawn_exec 命令拉起 zcode/shell 子进程，
// stdout/stderr 按行以事件回推（exec-out:<id>），结束时发 exec-done:<id>。
// 前端不思考，只把这些流式转发给后端（见 src/App.tsx 的 runExec）。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::{Command, Stdio};

use tauri::Emitter;

const DEFAULT_ZCODE_APP: &str = "/Applications/ZCode.app/Contents/MacOS/ZCode";
const DEFAULT_ZCODE_CJS: &str = "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs";

fn zcode_paths() -> (String, String) {
    (
        std::env::var("PAA_ZCODE_APP").unwrap_or_else(|_| DEFAULT_ZCODE_APP.into()),
        std::env::var("PAA_ZCODE").unwrap_or_else(|_| DEFAULT_ZCODE_CJS.into()),
    )
}

#[tauri::command]
async fn spawn_exec(
    app: tauri::AppHandle,
    exec_id: String,
    kind: String,
    prompt: String,
    command: String,
    cwd: String,
) -> Result<(), String> {
    let (zcode_app, zcode_cjs) = zcode_paths();
    let mut cmd = match kind.as_str() {
        // 关键：优先用 ZCode.app 内置 Electron 以 ELECTRON_RUN_AS_NODE 作为
        // node 运行时跑 CLI——系统 node 版本不匹配时 zcode 的 SQLite 迁移会失败。
        "zcode" => {
            let mut c = if Path::new(&zcode_app).exists() {
                let mut c = Command::new(&zcode_app);
                c.env("ELECTRON_RUN_AS_NODE", "1");
                c
            } else {
                Command::new("node")
            };
            c.arg(&zcode_cjs)
                .arg("-p")
                .arg(&prompt)
                .arg("--cwd")
                .arg(&cwd);
            c
        }
        "shell" => {
            let mut c = Command::new("/bin/zsh");
            c.arg("-lc").arg(&command);
            c
        }
        _ => return Err(format!("不支持的能力：{kind}")),
    };

    let _ = std::fs::create_dir_all(&cwd);
    std::thread::spawn(move || {
        let child = cmd
            .current_dir(&cwd)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn();
        match child {
            Ok(mut child) => {
                if let Some(err) = child.stderr.take() {
                    let app2 = app.clone();
                    let id2 = exec_id.clone();
                    std::thread::spawn(move || {
                        for line in BufReader::new(err).lines().map_while(|l| l.ok()) {
                            let _ = app2.emit(&format!("exec-out:{id2}"), line);
                        }
                    });
                }
                if let Some(out) = child.stdout.take() {
                    for line in BufReader::new(out).lines().map_while(|l| l.ok()) {
                        let _ = app.emit(&format!("exec-out:{exec_id}"), line);
                    }
                }
                match child.wait() {
                    Ok(s) => {
                        let _ = app.emit(
                            &format!("exec-done:{exec_id}"),
                            serde_json::json!({ "ok": s.success(), "exit_code": s.code(), "error": "" }),
                        );
                    }
                    Err(e) => {
                        let _ = app.emit(
                            &format!("exec-done:{exec_id}"),
                            serde_json::json!({ "ok": false, "exit_code": null, "error": e.to_string() }),
                        );
                    }
                }
            }
            Err(e) => {
                let _ = app.emit(
                    &format!("exec-done:{exec_id}"),
                    serde_json::json!({ "ok": false, "exit_code": null, "error": e.to_string() }),
                );
            }
        }
    });
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![spawn_exec])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
