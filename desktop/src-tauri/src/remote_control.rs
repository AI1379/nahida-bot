use serde::{Deserialize, Deserializer, Serialize};
use serde_json::{json, Map, Value};
use std::collections::HashSet;
use std::path::{Component, Path, PathBuf};
use std::process::Stdio;
use std::sync::RwLock;
use tauri::{AppHandle, Manager, WebviewWindow};
use tokio::io::AsyncReadExt;
use tokio::process::Command;
use tokio::time::{timeout, Duration};

pub const PROCESS_CAPABILITY: &str = "desktop.process.exec";
pub const READ_TEXT_CAPABILITY: &str = "desktop.fs.read_text";

const POLICY_FILE: &str = "remote-control-policy.json";
const HARD_MAX_TIMEOUT_MS: u64 = 30_000;
const HARD_MAX_OUTPUT_BYTES: u64 = 1_048_576;
const HARD_MAX_FILE_BYTES: u64 = 1_048_576;
const HARD_MAX_ADDITIONAL_ARGS: u32 = 64;
const HARD_MAX_TOTAL_ARGS: usize = 128;
const HARD_MAX_ARG_BYTES: u64 = 8_192;
const HARD_MAX_ACTORS: usize = 32;
const HARD_MAX_ROOTS: usize = 32;
const HARD_MAX_PROFILES: usize = 64;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ReadRoot {
    pub id: String,
    pub path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ExecProfile {
    pub id: String,
    pub program: String,
    #[serde(default)]
    pub fixed_args: Vec<String>,
    pub cwd_root_id: String,
    #[serde(default)]
    pub allow_additional_args: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RemoteControlLimits {
    pub timeout_ms: u64,
    pub output_limit_bytes: u64,
    pub stdout_limit_bytes: u64,
    pub stderr_limit_bytes: u64,
    pub file_limit_bytes: u64,
    pub max_additional_args: u32,
    pub max_arg_bytes: u64,
}

impl Default for RemoteControlLimits {
    fn default() -> Self {
        Self {
            timeout_ms: 10_000,
            output_limit_bytes: 262_144,
            stdout_limit_bytes: 131_072,
            stderr_limit_bytes: 131_072,
            file_limit_bytes: 262_144,
            max_additional_args: 16,
            max_arg_bytes: 4_096,
        }
    }
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RemoteControlMode {
    #[default]
    Disabled,
    Scoped,
    FullAccess,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RemoteControlPolicy {
    pub mode: RemoteControlMode,
    #[serde(default)]
    pub allowed_actor_account_keys: Vec<String>,
    #[serde(default)]
    pub read_roots: Vec<ReadRoot>,
    #[serde(default)]
    pub exec_profiles: Vec<ExecProfile>,
    #[serde(default)]
    pub limits: RemoteControlLimits,
}

impl Default for RemoteControlPolicy {
    fn default() -> Self {
        Self {
            mode: RemoteControlMode::Disabled,
            allowed_actor_account_keys: Vec::new(),
            read_roots: Vec::new(),
            exec_profiles: Vec::new(),
            limits: RemoteControlLimits::default(),
        }
    }
}

impl<'de> Deserialize<'de> for RemoteControlPolicy {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        #[derive(Deserialize)]
        #[serde(rename_all = "camelCase", deny_unknown_fields)]
        struct StoredPolicy {
            #[serde(default)]
            mode: Option<RemoteControlMode>,
            #[serde(default)]
            enabled: Option<bool>,
            #[serde(default)]
            allowed_actor_account_keys: Vec<String>,
            #[serde(default)]
            read_roots: Vec<ReadRoot>,
            #[serde(default)]
            exec_profiles: Vec<ExecProfile>,
            #[serde(default)]
            limits: RemoteControlLimits,
        }

        let stored = StoredPolicy::deserialize(deserializer)?;
        if stored.mode.is_some() && stored.enabled.is_some() {
            return Err(serde::de::Error::custom(
                "policy may contain mode or legacy enabled, not both",
            ));
        }
        Ok(Self {
            mode: stored.mode.unwrap_or(match stored.enabled {
                Some(true) => RemoteControlMode::Scoped,
                _ => RemoteControlMode::Disabled,
            }),
            allowed_actor_account_keys: stored.allowed_actor_account_keys,
            read_roots: stored.read_roots,
            exec_profiles: stored.exec_profiles,
            limits: stored.limits,
        })
    }
}

pub struct RemoteControlManager {
    path: PathBuf,
    policy: RwLock<RemoteControlPolicy>,
}

impl RemoteControlManager {
    pub fn load(app: &AppHandle) -> Result<Self, String> {
        let directory = app
            .path()
            .app_config_dir()
            .map_err(|error| format!("could not resolve app config directory: {error}"))?;
        Self::load_from(directory.join(POLICY_FILE))
    }

    fn load_from(path: PathBuf) -> Result<Self, String> {
        let policy = match std::fs::read(&path) {
            Ok(raw) => serde_json::from_slice(&raw)
                .map_err(|error| format!("remote control policy is invalid: {error}"))?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                RemoteControlPolicy::default()
            }
            Err(error) => return Err(format!("could not read remote control policy: {error}")),
        };
        validate_policy(&policy)?;
        Ok(Self {
            path,
            policy: RwLock::new(policy),
        })
    }

    fn snapshot(&self) -> RemoteControlPolicy {
        self.policy
            .read()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone()
    }

    fn save(&self, policy: RemoteControlPolicy) -> Result<(), String> {
        validate_policy(&policy)?;
        let parent = self
            .path
            .parent()
            .ok_or_else(|| "remote control policy path has no parent".to_string())?;
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("could not create policy directory: {error}"))?;
        let raw = serde_json::to_vec_pretty(&policy)
            .map_err(|error| format!("could not serialize remote control policy: {error}"))?;
        let temporary = self.path.with_extension("json.tmp");
        std::fs::write(&temporary, raw)
            .map_err(|error| format!("could not write remote control policy: {error}"))?;
        let backup = self.path.with_extension("json.bak");
        if backup.exists() {
            std::fs::remove_file(&backup)
                .map_err(|error| format!("could not remove stale policy backup: {error}"))?;
        }
        if self.path.exists() {
            std::fs::rename(&self.path, &backup)
                .map_err(|error| format!("could not prepare policy replacement: {error}"))?;
        }
        if let Err(error) = std::fs::rename(&temporary, &self.path) {
            if backup.exists() {
                let _ = std::fs::rename(&backup, &self.path);
            }
            return Err(format!("could not commit remote control policy: {error}"));
        }
        if backup.exists() {
            let _ = std::fs::remove_file(&backup);
        }
        *self
            .policy
            .write()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = policy;
        Ok(())
    }
}

#[tauri::command]
pub fn remote_control_policy_read(
    window: WebviewWindow,
    state: tauri::State<'_, RemoteControlManager>,
) -> Result<RemoteControlPolicy, String> {
    require_main_window(&window)?;
    Ok(state.snapshot())
}

#[tauri::command]
pub fn remote_control_policy_save(
    window: WebviewWindow,
    state: tauri::State<'_, RemoteControlManager>,
    policy: RemoteControlPolicy,
) -> Result<(), String> {
    require_main_window(&window)?;
    state.save(policy)
}

fn require_main_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() == "main" {
        Ok(())
    } else {
        Err("remote control policy is only available to the main window".to_string())
    }
}

#[derive(Debug)]
pub struct RemoteControlError {
    pub code: &'static str,
    pub message: String,
    pub details: Map<String, Value>,
}

impl RemoteControlError {
    fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            details: Map::new(),
        }
    }

    fn detail(mut self, key: &str, value: impl Into<Value>) -> Self {
        self.details.insert(key.to_string(), value.into());
        self
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RunProfileArguments {
    actor_account_key: String,
    program: String,
    #[serde(default)]
    args: Vec<String>,
    #[serde(default)]
    cwd: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ReadTextArguments {
    actor_account_key: String,
    path: String,
    #[serde(default)]
    root_id: Option<String>,
    #[serde(default)]
    offset: u64,
    max_bytes: u64,
}

pub async fn execute(
    app: &AppHandle,
    capability: &str,
    arguments: Map<String, Value>,
) -> Result<Value, RemoteControlError> {
    let policy = app.state::<RemoteControlManager>().snapshot();
    match capability {
        PROCESS_CAPABILITY => {
            let arguments = serde_json::from_value(Value::Object(arguments)).map_err(|error| {
                RemoteControlError::new(
                    "invalid_arguments",
                    format!("invalid process arguments: {error}"),
                )
            })?;
            run_profile(&policy, arguments).await
        }
        READ_TEXT_CAPABILITY => {
            let arguments = serde_json::from_value(Value::Object(arguments)).map_err(|error| {
                RemoteControlError::new(
                    "invalid_arguments",
                    format!("invalid file arguments: {error}"),
                )
            })?;
            read_text(&policy, arguments).await
        }
        _ => Err(RemoteControlError::new(
            "capability_not_found",
            "remote control capability is not registered",
        )),
    }
}

async fn run_profile(
    policy: &RemoteControlPolicy,
    arguments: RunProfileArguments,
) -> Result<Value, RemoteControlError> {
    authorize(policy, &arguments.actor_account_key)?;
    let (program, fixed_args, cwd, clear_environment) = match policy.mode {
        RemoteControlMode::Disabled => unreachable!("authorize rejects disabled mode"),
        RemoteControlMode::Scoped => {
            let profile = policy
                .exec_profiles
                .iter()
                .find(|profile| profile.id == arguments.program)
                .ok_or_else(|| {
                    RemoteControlError::new(
                        "profile_not_found",
                        "program is not an allowed local profile id",
                    )
                })?;
            if !profile.allow_additional_args && !arguments.args.is_empty() {
                return Err(RemoteControlError::new(
                    "additional_args_denied",
                    "this profile does not allow additional arguments",
                ));
            }
            let root = resolve_root(policy, &profile.cwd_root_id)?;
            let cwd = resolve_within_root(&root, &arguments.cwd, true)?;
            let program = std::fs::canonicalize(&profile.program).map_err(|error| {
                RemoteControlError::new(
                    "program_unavailable",
                    format!("could not resolve profile program: {error}"),
                )
            })?;
            if !program.is_file() {
                return Err(RemoteControlError::new(
                    "program_unavailable",
                    "profile program is not a regular file",
                ));
            }
            (program, profile.fixed_args.as_slice(), Some(cwd), true)
        }
        RemoteControlMode::FullAccess => {
            if arguments.program.trim().is_empty() || arguments.program.contains('\0') {
                return Err(RemoteControlError::new(
                    "invalid_arguments",
                    "program must be non-empty and may not contain NUL",
                ));
            }
            let cwd = if arguments.cwd.is_empty() {
                None
            } else {
                let cwd = std::fs::canonicalize(&arguments.cwd).map_err(|error| {
                    RemoteControlError::new(
                        "path_unavailable",
                        format!("could not resolve working directory: {error}"),
                    )
                })?;
                if !cwd.is_dir() {
                    return Err(RemoteControlError::new(
                        "invalid_directory",
                        "working directory is not a directory",
                    ));
                }
                Some(cwd)
            };
            (PathBuf::from(&arguments.program), &[][..], cwd, false)
        }
    };
    if arguments.args.len() > policy.limits.max_additional_args as usize
        || fixed_args.len() + arguments.args.len() > HARD_MAX_TOTAL_ARGS
    {
        return Err(RemoteControlError::new(
            "argument_limit_exceeded",
            "too many arguments",
        ));
    }
    for argument in fixed_args.iter().chain(arguments.args.iter()) {
        if argument.len() as u64 > policy.limits.max_arg_bytes || argument.contains('\0') {
            return Err(RemoteControlError::new(
                "argument_limit_exceeded",
                "an argument exceeds the configured limit or contains NUL",
            ));
        }
    }

    let mut command = Command::new(program);
    command
        .args(fixed_args)
        .args(&arguments.args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    if let Some(cwd) = cwd {
        command.current_dir(cwd);
    }
    if clear_environment {
        command.env_clear();
    }
    let mut child = command.spawn().map_err(|error| {
        RemoteControlError::new(
            "process_start_failed",
            format!("could not start profile: {error}"),
        )
    })?;
    let stdout = child.stdout.take().expect("stdout was piped");
    let stderr = child.stderr.take().expect("stderr was piped");
    let stdout_limit = policy.limits.stdout_limit_bytes;
    let stderr_limit = policy.limits.stderr_limit_bytes;
    let stdout_task = tokio::spawn(read_bounded(stdout, stdout_limit));
    let stderr_task = tokio::spawn(read_bounded(stderr, stderr_limit));

    let status = match timeout(
        Duration::from_millis(policy.limits.timeout_ms),
        child.wait(),
    )
    .await
    {
        Ok(result) => result.map_err(|error| {
            RemoteControlError::new(
                "process_failed",
                format!("could not wait for profile: {error}"),
            )
        })?,
        Err(_) => {
            let _ = child.kill().await;
            let _ = child.wait().await;
            return Err(
                RemoteControlError::new("process_timeout", "profile execution timed out")
                    .detail("timeoutMs", policy.limits.timeout_ms),
            );
        }
    };
    let stdout = stdout_task
        .await
        .map_err(|_| RemoteControlError::new("process_failed", "stdout reader failed"))?
        .map_err(|error| {
            RemoteControlError::new("process_failed", format!("could not read stdout: {error}"))
        })?;
    let stderr = stderr_task
        .await
        .map_err(|_| RemoteControlError::new("process_failed", "stderr reader failed"))?
        .map_err(|error| {
            RemoteControlError::new("process_failed", format!("could not read stderr: {error}"))
        })?;
    if stdout.len() as u64 > stdout_limit
        || stderr.len() as u64 > stderr_limit
        || (stdout.len() + stderr.len()) as u64 > policy.limits.output_limit_bytes
    {
        return Err(RemoteControlError::new(
            "output_limit_exceeded",
            "profile output exceeded the configured hard limit",
        ));
    }

    Ok(json!({
        "program": arguments.program,
        "exitCode": status.code(),
        "success": status.success(),
        "stdout": String::from_utf8_lossy(&stdout),
        "stderr": String::from_utf8_lossy(&stderr),
    }))
}

async fn read_bounded<R: tokio::io::AsyncRead + Unpin>(
    reader: R,
    limit: u64,
) -> std::io::Result<Vec<u8>> {
    let mut bytes = Vec::with_capacity(limit.min(65_536) as usize);
    reader.take(limit + 1).read_to_end(&mut bytes).await?;
    Ok(bytes)
}

async fn read_text(
    policy: &RemoteControlPolicy,
    arguments: ReadTextArguments,
) -> Result<Value, RemoteControlError> {
    authorize(policy, &arguments.actor_account_key)?;
    if arguments.max_bytes == 0 || arguments.max_bytes > policy.limits.file_limit_bytes {
        return Err(RemoteControlError::new(
            "file_limit_exceeded",
            "maxBytes must be positive and no greater than the configured file limit",
        ));
    }
    let path = match policy.mode {
        RemoteControlMode::Disabled => unreachable!("authorize rejects disabled mode"),
        RemoteControlMode::Scoped => {
            let root_id = arguments.root_id.as_deref().ok_or_else(|| {
                RemoteControlError::new("invalid_arguments", "rootId is required in scoped mode")
            })?;
            let root = resolve_root(policy, root_id)?;
            resolve_within_root(&root, &arguments.path, false)?
        }
        RemoteControlMode::FullAccess => {
            std::fs::canonicalize(&arguments.path).map_err(|error| {
                RemoteControlError::new(
                    "path_unavailable",
                    format!("could not resolve file path: {error}"),
                )
            })?
        }
    };
    let metadata = tokio::fs::metadata(&path).await.map_err(|error| {
        RemoteControlError::new(
            "file_read_failed",
            format!("could not inspect file: {error}"),
        )
    })?;
    if !metadata.is_file() {
        return Err(RemoteControlError::new(
            "invalid_file",
            "path is not a regular file",
        ));
    }
    if metadata.len() > policy.limits.file_limit_bytes {
        return Err(RemoteControlError::new(
            "file_limit_exceeded",
            "file exceeds the configured hard limit",
        ));
    }
    let bytes = tokio::fs::read(&path).await.map_err(|error| {
        RemoteControlError::new("file_read_failed", format!("could not read file: {error}"))
    })?;
    let text = std::str::from_utf8(&bytes)
        .map_err(|_| RemoteControlError::new("invalid_file", "file is not valid UTF-8 text"))?;
    let offset = usize::try_from(arguments.offset)
        .map_err(|_| RemoteControlError::new("invalid_arguments", "offset is out of range"))?;
    if offset > bytes.len() || !text.is_char_boundary(offset) {
        return Err(RemoteControlError::new(
            "invalid_arguments",
            "offset must be a UTF-8 character boundary within the file",
        ));
    }
    let requested_end = offset
        .saturating_add(arguments.max_bytes as usize)
        .min(bytes.len());
    let mut end = requested_end;
    while end > offset && !text.is_char_boundary(end) {
        end -= 1;
    }
    Ok(json!({
        "rootId": arguments.root_id,
        "path": arguments.path,
        "content": &text[offset..end],
        "offset": arguments.offset,
        "bytesRead": end - offset,
        "nextOffset": end,
        "eof": end == bytes.len(),
    }))
}

fn authorize(policy: &RemoteControlPolicy, actor: &str) -> Result<(), RemoteControlError> {
    if policy.mode == RemoteControlMode::Disabled {
        return Err(RemoteControlError::new(
            "remote_control_disabled",
            "local remote control is disabled",
        ));
    }
    let actor = actor.trim();
    if actor.is_empty() {
        return Err(RemoteControlError::new(
            "actor_required",
            "Gateway-injected actorAccountKey is required",
        ));
    }
    if !policy
        .allowed_actor_account_keys
        .iter()
        .any(|allowed| allowed == actor)
    {
        return Err(RemoteControlError::new(
            "actor_denied",
            "actorAccountKey is not locally pre-authorized",
        ));
    }
    Ok(())
}

fn resolve_root(policy: &RemoteControlPolicy, id: &str) -> Result<PathBuf, RemoteControlError> {
    let root = policy
        .read_roots
        .iter()
        .find(|root| root.id == id)
        .ok_or_else(|| RemoteControlError::new("root_not_found", "root is not allowed"))?;
    std::fs::canonicalize(&root.path).map_err(|error| {
        RemoteControlError::new(
            "root_unavailable",
            format!("could not resolve root: {error}"),
        )
    })
}

fn resolve_within_root(
    root: &Path,
    relative: &str,
    require_directory: bool,
) -> Result<PathBuf, RemoteControlError> {
    validate_relative_path(relative)?;
    let candidate = if relative.is_empty() {
        root.to_path_buf()
    } else {
        root.join(relative)
    };
    let canonical = std::fs::canonicalize(&candidate).map_err(|error| {
        RemoteControlError::new(
            "path_unavailable",
            format!("could not resolve path: {error}"),
        )
    })?;
    if !canonical.starts_with(root) {
        return Err(RemoteControlError::new(
            "path_denied",
            "resolved path escapes the configured root",
        ));
    }
    if require_directory && !canonical.is_dir() {
        return Err(RemoteControlError::new(
            "invalid_directory",
            "working directory is not a directory",
        ));
    }
    Ok(canonical)
}

fn validate_relative_path(relative: &str) -> Result<(), RemoteControlError> {
    if relative.contains(':') || relative.starts_with(['/', '\\']) {
        return Err(RemoteControlError::new(
            "invalid_path",
            "path must be relative and may not contain a drive, ADS, UNC, or device prefix",
        ));
    }
    let path = Path::new(relative);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        if relative.is_empty() {
            return Ok(());
        }
        return Err(RemoteControlError::new(
            "invalid_path",
            "path may only contain normal relative components",
        ));
    }
    Ok(())
}

fn validate_policy(policy: &RemoteControlPolicy) -> Result<(), String> {
    let limits = &policy.limits;
    if limits.timeout_ms == 0 || limits.timeout_ms > HARD_MAX_TIMEOUT_MS {
        return Err(format!(
            "timeoutMs must be between 1 and {HARD_MAX_TIMEOUT_MS}"
        ));
    }
    if limits.output_limit_bytes == 0 || limits.output_limit_bytes > HARD_MAX_OUTPUT_BYTES {
        return Err(format!(
            "outputLimitBytes must be between 1 and {HARD_MAX_OUTPUT_BYTES}"
        ));
    }
    if limits.stdout_limit_bytes == 0
        || limits.stderr_limit_bytes == 0
        || limits.stdout_limit_bytes > limits.output_limit_bytes
        || limits.stderr_limit_bytes > limits.output_limit_bytes
        || limits
            .stdout_limit_bytes
            .checked_add(limits.stderr_limit_bytes)
            .is_none_or(|sum| sum > limits.output_limit_bytes)
    {
        return Err(
            "stdout/stderr limits must be positive and no greater than outputLimitBytes"
                .to_string(),
        );
    }
    if limits.file_limit_bytes == 0 || limits.file_limit_bytes > HARD_MAX_FILE_BYTES {
        return Err(format!(
            "fileLimitBytes must be between 1 and {HARD_MAX_FILE_BYTES}"
        ));
    }
    if limits.max_additional_args > HARD_MAX_ADDITIONAL_ARGS
        || limits.max_arg_bytes == 0
        || limits.max_arg_bytes > HARD_MAX_ARG_BYTES
    {
        return Err("argument limits exceed the application hard limits".to_string());
    }
    if policy.allowed_actor_account_keys.len() > HARD_MAX_ACTORS
        || policy.read_roots.len() > HARD_MAX_ROOTS
        || policy.exec_profiles.len() > HARD_MAX_PROFILES
    {
        return Err("remote control policy contains too many entries".to_string());
    }
    validate_unique_ids(
        policy.read_roots.iter().map(|root| root.id.as_str()),
        "read root",
    )?;
    validate_unique_ids(
        policy
            .exec_profiles
            .iter()
            .map(|profile| profile.id.as_str()),
        "execution profile",
    )?;
    let roots: HashSet<&str> = policy
        .read_roots
        .iter()
        .map(|root| root.id.as_str())
        .collect();
    for root in &policy.read_roots {
        if root.path.trim().is_empty() || !Path::new(&root.path).is_absolute() {
            return Err(format!("read root {} must have an absolute path", root.id));
        }
    }
    for actor in &policy.allowed_actor_account_keys {
        if actor.trim().is_empty() || actor != actor.trim() {
            return Err(
                "allowedActorAccountKeys may not contain blank or padded values".to_string(),
            );
        }
    }
    for profile in &policy.exec_profiles {
        let program = Path::new(&profile.program);
        if profile.program.trim().is_empty() || !program.is_absolute() {
            return Err(format!(
                "execution profile {} program must be an absolute path",
                profile.id
            ));
        }
        if is_forbidden_interpreter(&profile.program) {
            return Err(format!(
                "execution profile {} uses a forbidden interpreter",
                profile.id
            ));
        }
        if profile.fixed_args.len() > HARD_MAX_TOTAL_ARGS {
            return Err(format!(
                "execution profile {} has too many fixed arguments",
                profile.id
            ));
        }
        for argument in &profile.fixed_args {
            if argument.contains('\0') || argument.len() as u64 > limits.max_arg_bytes {
                return Err(format!(
                    "execution profile {} has an invalid fixed argument",
                    profile.id
                ));
            }
        }
        if !roots.contains(profile.cwd_root_id.as_str()) {
            return Err(format!(
                "execution profile {} references an unknown cwdRootId",
                profile.id
            ));
        }
    }
    Ok(())
}

fn validate_unique_ids<'a>(ids: impl Iterator<Item = &'a str>, kind: &str) -> Result<(), String> {
    let mut seen = HashSet::new();
    for id in ids {
        if id.trim().is_empty() || id != id.trim() || !seen.insert(id) {
            return Err(format!("{kind} ids must be non-empty, trimmed, and unique"));
        }
    }
    Ok(())
}

fn is_forbidden_interpreter(program: &str) -> bool {
    let trimmed = program.trim();
    let path = Path::new(trimmed);
    if path
        .extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| {
            matches!(
                extension.to_ascii_lowercase().as_str(),
                "bat" | "cmd" | "ps1" | "vbs" | "vbe" | "js" | "jse" | "wsf" | "wsh"
            )
        })
    {
        return true;
    }
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(trimmed)
        .to_ascii_lowercase();
    let name = name.strip_suffix(".exe").unwrap_or(&name);
    matches!(
        name,
        "cmd"
            | "powershell"
            | "pwsh"
            | "wscript"
            | "cscript"
            | "sh"
            | "bash"
            | "zsh"
            | "fish"
            | "python"
            | "python3"
            | "pythonw"
            | "node"
            | "deno"
            | "bun"
            | "ruby"
            | "perl"
            | "php"
            | "lua"
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temporary_directory(name: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("nahida-remote-control-{name}-{nonce}"));
        std::fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn defaults_to_disabled_and_persists_valid_policy() {
        let directory = temporary_directory("policy");
        let path = directory.join(POLICY_FILE);
        let manager = RemoteControlManager::load_from(path.clone()).unwrap();
        assert_eq!(manager.snapshot().mode, RemoteControlMode::Disabled);
        let mut policy = manager.snapshot();
        policy
            .allowed_actor_account_keys
            .push("telegram:user:1".to_string());
        manager.save(policy.clone()).unwrap();
        assert_eq!(
            RemoteControlManager::load_from(path).unwrap().snapshot(),
            policy
        );
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn migrates_legacy_enabled_policy_to_scoped() {
        let policy: RemoteControlPolicy = serde_json::from_value(json!({
            "enabled": true,
            "allowedActorAccountKeys": [],
            "readRoots": [],
            "execProfiles": [],
            "limits": RemoteControlLimits::default(),
        }))
        .unwrap();
        assert_eq!(policy.mode, RemoteControlMode::Scoped);
        assert!(serde_json::to_value(policy)
            .unwrap()
            .get("enabled")
            .is_none());
    }

    #[test]
    fn rejects_interpreter_profiles_and_unknown_roots() {
        let mut policy = RemoteControlPolicy::default();
        policy.exec_profiles.push(ExecProfile {
            id: "bad".to_string(),
            program: std::env::temp_dir()
                .join("PowerShell.EXE")
                .to_string_lossy()
                .into_owned(),
            fixed_args: vec![],
            cwd_root_id: "missing".to_string(),
            allow_additional_args: false,
        });
        assert!(validate_policy(&policy)
            .unwrap_err()
            .contains("forbidden interpreter"));
    }

    #[test]
    fn rejects_unsafe_relative_paths() {
        for path in [
            "../secret",
            "C:\\secret",
            "\\\\server\\share",
            "file.txt:stream",
            ".\\file",
        ] {
            assert!(validate_relative_path(path).is_err(), "{path}");
        }
        assert!(validate_relative_path("folder/file.txt").is_ok());
        assert!(validate_relative_path("").is_ok());
    }

    #[tokio::test]
    async fn reads_only_authorized_utf8_files_within_root() {
        let directory = temporary_directory("read");
        std::fs::write(directory.join("hello.txt"), "草 hello").unwrap();
        let mut policy = RemoteControlPolicy {
            mode: RemoteControlMode::Scoped,
            ..RemoteControlPolicy::default()
        };
        policy
            .allowed_actor_account_keys
            .push("telegram:user:1".to_string());
        policy.read_roots.push(ReadRoot {
            id: "notes".to_string(),
            path: directory.to_string_lossy().into_owned(),
        });
        let result = read_text(
            &policy,
            ReadTextArguments {
                actor_account_key: "telegram:user:1".to_string(),
                path: "hello.txt".to_string(),
                root_id: Some("notes".to_string()),
                offset: 0,
                max_bytes: 4,
            },
        )
        .await
        .unwrap();
        assert_eq!(result["content"], "草 ");
        assert_eq!(result["bytesRead"], 4);
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[tokio::test]
    async fn runs_only_a_locally_authorized_profile_without_a_shell() {
        let directory = temporary_directory("process");
        let mut policy = RemoteControlPolicy {
            mode: RemoteControlMode::Scoped,
            ..RemoteControlPolicy::default()
        };
        policy
            .allowed_actor_account_keys
            .push("telegram:user:1".to_string());
        policy.read_roots.push(ReadRoot {
            id: "work".to_string(),
            path: directory.to_string_lossy().into_owned(),
        });
        policy.exec_profiles.push(ExecProfile {
            id: "rust-version".to_string(),
            program: std::env::current_exe()
                .unwrap()
                .to_string_lossy()
                .into_owned(),
            fixed_args: vec!["--help".to_string()],
            cwd_root_id: "work".to_string(),
            allow_additional_args: false,
        });

        let result = run_profile(
            &policy,
            RunProfileArguments {
                actor_account_key: "telegram:user:1".to_string(),
                program: "rust-version".to_string(),
                args: vec![],
                cwd: String::new(),
            },
        )
        .await
        .unwrap();
        assert_eq!(result["success"], true);
        assert!(result["success"].as_bool().unwrap());

        std::fs::remove_dir_all(directory).unwrap();
    }

    #[tokio::test]
    async fn full_access_reads_without_root_and_runs_arbitrary_program() {
        let directory = temporary_directory("full-access");
        let file = directory.join("hello.txt");
        std::fs::write(&file, "hello").unwrap();
        let mut policy = RemoteControlPolicy {
            mode: RemoteControlMode::FullAccess,
            ..RemoteControlPolicy::default()
        };
        policy
            .allowed_actor_account_keys
            .push("telegram:user:1".to_string());

        let read = read_text(
            &policy,
            ReadTextArguments {
                actor_account_key: "telegram:user:1".to_string(),
                path: file.to_string_lossy().into_owned(),
                root_id: None,
                offset: 0,
                max_bytes: 5,
            },
        )
        .await
        .unwrap();
        assert_eq!(read["content"], "hello");

        let run = run_profile(
            &policy,
            RunProfileArguments {
                actor_account_key: "telegram:user:1".to_string(),
                program: std::env::current_exe()
                    .unwrap()
                    .to_string_lossy()
                    .into_owned(),
                args: vec!["--help".to_string()],
                cwd: directory.to_string_lossy().into_owned(),
            },
        )
        .await
        .unwrap();
        assert_eq!(run["success"], true);
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[tokio::test]
    async fn disabled_mode_rejects_remote_control() {
        let error = read_text(
            &RemoteControlPolicy::default(),
            ReadTextArguments {
                actor_account_key: "telegram:user:1".to_string(),
                path: "anything".to_string(),
                root_id: None,
                offset: 0,
                max_bytes: 1,
            },
        )
        .await
        .unwrap_err();
        assert_eq!(error.code, "remote_control_disabled");
    }
}
