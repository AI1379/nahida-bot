use serde_json::Value;
use std::{collections::HashMap, io::ErrorKind, path::PathBuf};
use tauri::{AppHandle, Manager, State};
use tokio::{fs, io::AsyncWriteExt, sync::Mutex};

const DATASET_KINDS: [&str; 4] = ["decisions", "executions", "preferences", "invalid"];
const MAXIMUM_RECORD_BYTES: usize = 262_144;

pub struct MotionDatasetManager {
    root: PathBuf,
    write_lock: Mutex<()>,
}

impl MotionDatasetManager {
    pub fn load(app: &AppHandle) -> Result<Self, String> {
        let root = app
            .path()
            .app_data_dir()
            .map_err(|error| format!("failed to resolve app data directory: {error}"))?
            .join("motion-dataset");
        Ok(Self {
            root,
            write_lock: Mutex::new(()),
        })
    }

    fn path_for_kind(&self, kind: &str) -> Result<PathBuf, String> {
        if !DATASET_KINDS.contains(&kind) {
            return Err(format!("unsupported motion dataset kind: {kind}"));
        }
        Ok(self.root.join(format!("{kind}.jsonl")))
    }

    async fn append(&self, kind: &str, record: &Value) -> Result<(), String> {
        let serialized = serde_json::to_string(record)
            .map_err(|error| format!("failed to serialize motion record: {error}"))?;
        if serialized.len() > MAXIMUM_RECORD_BYTES {
            return Err("motion dataset record exceeds 256 KiB".to_string());
        }
        let path = self.path_for_kind(kind)?;
        let _guard = self.write_lock.lock().await;
        fs::create_dir_all(&self.root)
            .await
            .map_err(|error| format!("failed to create motion dataset directory: {error}"))?;
        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .await
            .map_err(|error| format!("failed to open {}: {error}", path.display()))?;
        file.write_all(serialized.as_bytes())
            .await
            .map_err(|error| format!("failed to append {}: {error}", path.display()))?;
        file.write_all(b"\n")
            .await
            .map_err(|error| format!("failed to append {}: {error}", path.display()))?;
        file.flush()
            .await
            .map_err(|error| format!("failed to flush {}: {error}", path.display()))
    }

    async fn read(&self, kind: &str) -> Result<Vec<Value>, String> {
        let path = self.path_for_kind(kind)?;
        let _guard = self.write_lock.lock().await;
        let source = match fs::read_to_string(&path).await {
            Ok(source) => source,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(Vec::new()),
            Err(error) => return Err(format!("failed to read {}: {error}", path.display())),
        };
        source
            .lines()
            .filter(|line| !line.trim().is_empty())
            .enumerate()
            .map(|(index, line)| {
                serde_json::from_str(line).map_err(|error| {
                    format!(
                        "invalid JSONL at {} line {}: {error}",
                        path.display(),
                        index + 1
                    )
                })
            })
            .collect()
    }

    async fn clear(&self, kind: Option<&str>) -> Result<(), String> {
        let targets = match kind {
            Some(kind) => vec![self.path_for_kind(kind)?],
            None => DATASET_KINDS
                .iter()
                .map(|kind| self.path_for_kind(kind))
                .collect::<Result<Vec<_>, _>>()?,
        };
        let _guard = self.write_lock.lock().await;
        for path in targets {
            match fs::remove_file(&path).await {
                Ok(()) => {}
                Err(error) if error.kind() == ErrorKind::NotFound => {}
                Err(error) => return Err(format!("failed to remove {}: {error}", path.display())),
            }
        }
        Ok(())
    }
}

#[tauri::command]
pub async fn motion_dataset_append(
    manager: State<'_, MotionDatasetManager>,
    kind: String,
    record: Value,
) -> Result<(), String> {
    manager.append(&kind, &record).await
}

#[tauri::command]
pub async fn motion_dataset_read(
    manager: State<'_, MotionDatasetManager>,
    kind: String,
) -> Result<Vec<Value>, String> {
    manager.read(&kind).await
}

#[tauri::command]
pub async fn motion_dataset_export(
    manager: State<'_, MotionDatasetManager>,
) -> Result<HashMap<String, String>, String> {
    let mut exported = HashMap::new();
    for kind in DATASET_KINDS {
        let records = manager.read(kind).await?;
        let lines = records
            .iter()
            .map(serde_json::to_string)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("failed to export {kind}: {error}"))?
            .join("\n");
        exported.insert(kind.to_string(), lines);
    }
    Ok(exported)
}

#[tauri::command]
pub async fn motion_dataset_clear(
    manager: State<'_, MotionDatasetManager>,
    kind: Option<String>,
) -> Result<(), String> {
    manager.clear(kind.as_deref()).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn test_manager() -> MotionDatasetManager {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock is valid")
            .as_nanos();
        MotionDatasetManager {
            root: std::env::temp_dir().join(format!("nahida-motion-dataset-{suffix}")),
            write_lock: Mutex::new(()),
        }
    }

    #[test]
    fn appends_reads_and_clears_jsonl_records() {
        let manager = test_manager();
        tauri::async_runtime::block_on(async {
            manager
                .append("decisions", &serde_json::json!({"id": 1}))
                .await
                .expect("record appends");
            manager
                .append("decisions", &serde_json::json!({"id": 2}))
                .await
                .expect("second record appends");
            assert_eq!(manager.read("decisions").await.unwrap().len(), 2);
            manager.clear(Some("decisions")).await.unwrap();
            assert!(manager.read("decisions").await.unwrap().is_empty());
            let _ = fs::remove_dir_all(&manager.root).await;
        });
    }

    #[test]
    fn rejects_unrecognized_dataset_kinds() {
        let manager = test_manager();
        assert!(manager.path_for_kind("../escape").is_err());
    }
}
