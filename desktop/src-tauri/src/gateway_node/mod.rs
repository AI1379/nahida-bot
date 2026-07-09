mod protocol;

use futures_util::stream::SplitSink;
use futures_util::{SinkExt, StreamExt};
pub use protocol::NodeEnvelope;
use protocol::{
    build_heartbeat, build_request, build_response, desktop_capabilities, next_request_id,
    protocol_error, unix_millis, CapabilityInvokePayload, EnvelopeKind, HeartbeatPayload,
    RegisterOkPayload, RegisterPayload,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::env;
use std::sync::Arc;
use std::time::Duration;
use tauri::async_runtime::JoinHandle;
use tauri::{AppHandle, Emitter, State};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;
use tokio::net::TcpStream;
use tokio::sync::{mpsc, oneshot, Mutex};
use tokio::time::{interval, sleep, timeout, Instant};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{connect_async, MaybeTlsStream, WebSocketStream};
use url::Url;

pub const FRONTEND_EVENT: &str = "nahida://gateway-node/event";
const DEFAULT_GATEWAY_WS_URL: &str = "ws://127.0.0.1:6185/api/nodes/ws";
const DEFAULT_NODE_ID: &str = "desktop-local";
const DEFAULT_DISPLAY_NAME: &str = "Nahida Desktop";

type WsStream = WebSocketStream<MaybeTlsStream<TcpStream>>;
type WsWrite = SplitSink<WsStream, Message>;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GatewayNodeConnectConfig {
    pub url: Option<String>,
    pub token: Option<String>,
    pub node_id: Option<String>,
    pub display_name: Option<String>,
    pub default_session_id: Option<String>,
    pub metadata: Option<Map<String, Value>>,
}

#[derive(Debug, Clone)]
struct ResolvedConnectConfig {
    url: String,
    token: String,
    node_id: String,
    display_name: String,
    default_session_id: Option<String>,
    metadata: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GatewayNodeStatus {
    pub connected: bool,
    pub registered: bool,
    pub node_id: String,
    pub gateway_url: String,
    pub session_id: Option<String>,
    pub default_session_id: Option<String>,
    pub last_error: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GatewayNodeInput {
    pub session_id: String,
    pub text: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum GatewayNodeFrontendEvent {
    StatusChanged {
        at: String,
        status: GatewayNodeStatus,
    },
    GatewayEvent {
        at: String,
        envelope: NodeEnvelope,
    },
    CapabilityInvoke {
        at: String,
        #[serde(rename = "invokeId")]
        invoke_id: String,
        capability: String,
        arguments: Map<String, Value>,
    },
}

enum ClientCommand {
    SubmitInput {
        session_id: String,
        text: String,
        respond_to: oneshot::Sender<Result<NodeEnvelope, String>>,
    },
    Stop,
}

#[derive(Default)]
struct ManagerInner {
    tx: Option<mpsc::Sender<ClientCommand>>,
    handle: Option<JoinHandle<()>>,
}

pub struct GatewayNodeManager {
    inner: Mutex<ManagerInner>,
    status: Arc<Mutex<GatewayNodeStatus>>,
}

impl Default for GatewayNodeManager {
    fn default() -> Self {
        Self {
            inner: Mutex::new(ManagerInner::default()),
            status: Arc::new(Mutex::new(GatewayNodeStatus {
                connected: false,
                registered: false,
                node_id: env_or("NAHIDA_DESKTOP_NODE_ID", DEFAULT_NODE_ID),
                gateway_url: env_or("NAHIDA_GATEWAY_WS_URL", DEFAULT_GATEWAY_WS_URL),
                session_id: None,
                default_session_id: env::var("NAHIDA_DESKTOP_SESSION_ID").ok(),
                last_error: None,
            })),
        }
    }
}

impl GatewayNodeManager {
    async fn connect(
        &self,
        app: AppHandle,
        config: Option<GatewayNodeConnectConfig>,
    ) -> Result<GatewayNodeStatus, String> {
        let resolved = resolve_config(config)?;
        let (tx, rx) = mpsc::channel(32);
        let initial_status = update_status(&self.status, &app, |status| {
            status.connected = false;
            status.registered = false;
            status.node_id = resolved.node_id.clone();
            status.gateway_url = resolved.url.clone();
            status.session_id = None;
            status.default_session_id = resolved.default_session_id.clone();
            status.last_error = None;
        })
        .await;

        {
            let mut inner = self.inner.lock().await;
            if let Some(previous_tx) = inner.tx.take() {
                let _ = previous_tx.try_send(ClientCommand::Stop);
            }
            if let Some(handle) = inner.handle.take() {
                handle.abort();
            }

            let status = self.status.clone();
            let task_app = app.clone();
            let task_config = resolved.clone();
            inner.tx = Some(tx);
            inner.handle = Some(tauri::async_runtime::spawn(async move {
                client_loop(task_app, task_config, rx, status).await;
            }));
        }

        Ok(initial_status)
    }

    async fn disconnect(&self, app: AppHandle) -> GatewayNodeStatus {
        let (tx, handle) = {
            let mut inner = self.inner.lock().await;
            (inner.tx.take(), inner.handle.take())
        };

        if let Some(tx) = tx {
            let _ = tx.send(ClientCommand::Stop).await;
        }
        if let Some(handle) = handle {
            handle.abort();
        }

        update_status(&self.status, &app, |status| {
            status.connected = false;
            status.registered = false;
            status.session_id = None;
            status.last_error = None;
        })
        .await
    }

    async fn status(&self) -> GatewayNodeStatus {
        self.status.lock().await.clone()
    }

    async fn submit_input(&self, input: GatewayNodeInput) -> Result<NodeEnvelope, String> {
        let tx = {
            let inner = self.inner.lock().await;
            inner
                .tx
                .clone()
                .ok_or_else(|| "gateway node is not running".to_string())?
        };

        let (respond_to, response) = oneshot::channel();
        tx.send(ClientCommand::SubmitInput {
            session_id: input.session_id,
            text: input.text,
            respond_to,
        })
        .await
        .map_err(|_| "gateway node command loop is closed".to_string())?;

        timeout(Duration::from_secs(35), response)
            .await
            .map_err(|_| "node.input.submit timed out".to_string())?
            .map_err(|_| "node.input.submit response channel closed".to_string())?
    }
}

#[tauri::command]
pub async fn gateway_node_connect(
    app: AppHandle,
    state: State<'_, GatewayNodeManager>,
    config: Option<GatewayNodeConnectConfig>,
) -> Result<GatewayNodeStatus, String> {
    state.connect(app, config).await
}

#[tauri::command]
pub async fn gateway_node_disconnect(
    app: AppHandle,
    state: State<'_, GatewayNodeManager>,
) -> Result<GatewayNodeStatus, String> {
    Ok(state.disconnect(app).await)
}

#[tauri::command]
pub async fn gateway_node_status(
    state: State<'_, GatewayNodeManager>,
) -> Result<GatewayNodeStatus, String> {
    Ok(state.status().await)
}

#[tauri::command]
pub async fn gateway_node_submit_input(
    state: State<'_, GatewayNodeManager>,
    input: GatewayNodeInput,
) -> Result<NodeEnvelope, String> {
    state.submit_input(input).await
}

async fn client_loop(
    app: AppHandle,
    config: ResolvedConnectConfig,
    mut rx: mpsc::Receiver<ClientCommand>,
    status: Arc<Mutex<GatewayNodeStatus>>,
) {
    let mut delay = Duration::from_secs(1);

    loop {
        match run_once(&app, &config, &mut rx, &status).await {
            Ok(RunExit::Stopped) => break,
            Ok(RunExit::Disconnected) => {
                update_status(&status, &app, |status| {
                    status.connected = false;
                    status.registered = false;
                    status.session_id = None;
                    status.last_error = None;
                })
                .await;
                delay = Duration::from_secs(1);
            }
            Err(error) => {
                update_status(&status, &app, |status| {
                    status.connected = false;
                    status.registered = false;
                    status.session_id = None;
                    status.last_error = Some(error);
                })
                .await;
            }
        }

        tokio::select! {
            _ = sleep(delay) => {},
            command = rx.recv() => {
                match command {
                    Some(ClientCommand::Stop) | None => break,
                    Some(ClientCommand::SubmitInput { respond_to, .. }) => {
                        let _ = respond_to.send(Err("gateway node is reconnecting".to_string()));
                    }
                }
            }
        }
        delay = std::cmp::min(delay.saturating_mul(2), Duration::from_secs(30));
    }

    update_status(&status, &app, |status| {
        status.connected = false;
        status.registered = false;
        status.session_id = None;
    })
    .await;
}

enum RunExit {
    Stopped,
    Disconnected,
}

async fn run_once(
    app: &AppHandle,
    config: &ResolvedConnectConfig,
    rx: &mut mpsc::Receiver<ClientCommand>,
    status: &Arc<Mutex<GatewayNodeStatus>>,
) -> Result<RunExit, String> {
    let connect_url = with_query_token(&config.url, &config.token)?;
    let (ws, _) = connect_async(connect_url)
        .await
        .map_err(|err| format!("gateway websocket connect failed: {err}"))?;
    let (mut write, mut read) = ws.split();

    update_status(status, app, |status| {
        status.connected = true;
        status.registered = false;
        status.session_id = None;
        status.last_error = None;
    })
    .await;

    let register_id = next_request_id("register");
    let register_payload = serde_json::to_value(RegisterPayload {
        node_id: config.node_id.clone(),
        display_name: config.display_name.clone(),
        node_type: "desktop".to_string(),
        capabilities: desktop_capabilities(),
        metadata: config.metadata.clone(),
    })
    .map_err(|err| format!("failed to build register payload: {err}"))?;
    send_envelope(
        &mut write,
        &build_request("node.register", register_id.clone(), Some(register_payload)),
    )
    .await?;

    let register_response =
        wait_for_register_response(&mut write, &mut read, rx, &register_id).await?;
    let register_ok = validate_register_response(register_response)?;

    update_status(status, app, |status| {
        status.registered = true;
        status.session_id = Some(register_ok.session_id.clone());
        status.last_error = None;
    })
    .await;

    let heartbeat_interval = Duration::from_millis(register_ok.heartbeat_interval_ms.max(1_000));
    let heartbeat_timeout = Duration::from_millis(
        register_ok
            .heartbeat_timeout_ms
            .max(register_ok.heartbeat_interval_ms.max(1_000)),
    );
    let mut heartbeat = interval(heartbeat_interval);
    let mut last_seen = Instant::now();
    let mut pending: HashMap<String, oneshot::Sender<Result<NodeEnvelope, String>>> =
        HashMap::new();

    loop {
        tokio::select! {
            message = read.next() => {
                let Some(message) = message else {
                    return Ok(RunExit::Disconnected);
                };
                let message =
                    message.map_err(|err| format!("gateway websocket read failed: {err}"))?;
                let Some(envelope) = parse_message(message, &mut write).await? else {
                    continue;
                };
                last_seen = Instant::now();
                handle_envelope(app, &mut write, &mut pending, envelope).await?;
            }
            command = rx.recv() => {
                match command {
                    Some(ClientCommand::Stop) | None => {
                        let _ = write.close().await;
                        return Ok(RunExit::Stopped);
                    }
                    Some(ClientCommand::SubmitInput { session_id, text, respond_to }) => {
                        let request_id = next_request_id("input");
                        let request = build_request(
                            "node.input.submit",
                            request_id.clone(),
                            Some(json!({ "session_id": session_id, "text": text })),
                        );
                        pending.insert(request_id.clone(), respond_to);
                        if let Err(error) = send_envelope(&mut write, &request).await {
                            if let Some(respond_to) = pending.remove(&request_id) {
                                let _ = respond_to.send(Err(error.clone()));
                            }
                            return Err(error);
                        }
                    }
                }
            }
            _ = heartbeat.tick() => {
                if last_seen.elapsed() > heartbeat_timeout {
                    return Err("gateway heartbeat timed out".to_string());
                }
                send_envelope(
                    &mut write,
                    &build_heartbeat("ping", Some(unix_millis() as u64), None),
                )
                .await?;
            }
        }
    }
}

async fn wait_for_register_response(
    write: &mut WsWrite,
    read: &mut futures_util::stream::SplitStream<WsStream>,
    rx: &mut mpsc::Receiver<ClientCommand>,
    register_id: &str,
) -> Result<NodeEnvelope, String> {
    let deadline = sleep(Duration::from_secs(15));
    tokio::pin!(deadline);

    loop {
        tokio::select! {
            _ = &mut deadline => {
                return Err("node.register timed out".to_string());
            }
            command = rx.recv() => {
                match command {
                    Some(ClientCommand::Stop) | None => {
                        let _ = write.close().await;
                        return Err("gateway node stopped before registration completed".to_string());
                    }
                    Some(ClientCommand::SubmitInput { respond_to, .. }) => {
                        let _ = respond_to.send(Err("gateway node is not registered".to_string()));
                    }
                }
            }
            message = read.next() => {
                let Some(message) = message else {
                    return Err("gateway websocket closed during registration".to_string());
                };
                let message =
                    message.map_err(|err| format!("gateway websocket read failed: {err}"))?;
                let Some(envelope) = parse_message(message, write).await? else {
                    continue;
                };
                match envelope.kind {
                    EnvelopeKind::Response if envelope.id.as_deref() == Some(register_id) => {
                        return Ok(envelope);
                    }
                    EnvelopeKind::Heartbeat => {
                        handle_heartbeat(write, &envelope).await?;
                    }
                    _ => {}
                }
            }
        }
    }
}

fn validate_register_response(envelope: NodeEnvelope) -> Result<RegisterOkPayload, String> {
    if envelope.ok != Some(true) {
        let message = envelope
            .error
            .map(|error| error.message)
            .unwrap_or_else(|| "registration rejected".to_string());
        return Err(message);
    }
    serde_json::from_value(
        envelope
            .payload
            .ok_or_else(|| "node.register response payload is missing".to_string())?,
    )
    .map_err(|err| format!("node.register response payload is invalid: {err}"))
}

async fn handle_envelope(
    app: &AppHandle,
    write: &mut WsWrite,
    pending: &mut HashMap<String, oneshot::Sender<Result<NodeEnvelope, String>>>,
    envelope: NodeEnvelope,
) -> Result<(), String> {
    match envelope.kind {
        EnvelopeKind::Heartbeat => handle_heartbeat(write, &envelope).await,
        EnvelopeKind::Event => {
            emit_frontend_event(
                app,
                GatewayNodeFrontendEvent::GatewayEvent {
                    at: now_rfc3339(),
                    envelope,
                },
            );
            Ok(())
        }
        EnvelopeKind::Response => {
            if let Some(request_id) = envelope.id.clone() {
                if let Some(respond_to) = pending.remove(&request_id) {
                    let _ = respond_to.send(Ok(envelope));
                }
            }
            Ok(())
        }
        EnvelopeKind::Request => handle_request(app, write, envelope).await,
    }
}

async fn handle_request(
    app: &AppHandle,
    write: &mut WsWrite,
    envelope: NodeEnvelope,
) -> Result<(), String> {
    let request_id = envelope.id.clone().unwrap_or_default();
    let method = envelope.method.as_deref().unwrap_or("");

    if method != "capability.invoke" && method != "capability.cancel" {
        return send_envelope(
            write,
            &build_response(
                request_id,
                false,
                None,
                Some(protocol_error(
                    "method_not_found",
                    format!("unknown method: {method}"),
                    false,
                )),
            ),
        )
        .await;
    }

    if method == "capability.cancel" {
        return send_envelope(
            write,
            &build_response(
                request_id,
                true,
                Some(json!({ "acknowledged": true })),
                None,
            ),
        )
        .await;
    }

    let payload: CapabilityInvokePayload =
        serde_json::from_value(envelope.payload.clone().unwrap_or(Value::Null)).map_err(|err| {
            format!("capability.invoke payload is invalid and cannot be acknowledged: {err}")
        })?;

    let known = desktop_capabilities()
        .iter()
        .any(|capability| capability.name == payload.capability);

    if !known {
        return send_envelope(
            write,
            &build_response(
                request_id,
                false,
                None,
                Some(protocol_error(
                    "capability_not_found",
                    format!("capability {} not registered", payload.capability),
                    false,
                )),
            ),
        )
        .await;
    }

    emit_frontend_event(
        app,
        GatewayNodeFrontendEvent::CapabilityInvoke {
            at: now_rfc3339(),
            invoke_id: payload.invoke_id,
            capability: payload.capability,
            arguments: payload.arguments,
        },
    );

    send_envelope(
        write,
        &build_response(request_id, true, Some(json!({ "applied": true })), None),
    )
    .await
}

async fn handle_heartbeat(write: &mut WsWrite, envelope: &NodeEnvelope) -> Result<(), String> {
    let Some(payload) = envelope.payload.clone() else {
        return Ok(());
    };
    let Ok(heartbeat) = serde_json::from_value::<HeartbeatPayload>(payload) else {
        return Ok(());
    };
    if heartbeat.heartbeat_type == "ping" {
        send_envelope(write, &build_heartbeat("pong", None, heartbeat.ts)).await?;
    }
    Ok(())
}

async fn parse_message(
    message: Message,
    write: &mut WsWrite,
) -> Result<Option<NodeEnvelope>, String> {
    match message {
        Message::Text(text) => serde_json::from_str::<NodeEnvelope>(&text)
            .map(Some)
            .map_err(|err| format!("gateway envelope is invalid: {err}")),
        Message::Binary(bytes) => serde_json::from_slice::<NodeEnvelope>(&bytes)
            .map(Some)
            .map_err(|err| format!("gateway envelope is invalid: {err}")),
        Message::Ping(bytes) => {
            write
                .send(Message::Pong(bytes))
                .await
                .map_err(|err| format!("failed to send websocket pong: {err}"))?;
            Ok(None)
        }
        Message::Pong(_) => Ok(None),
        Message::Close(_) => Err("gateway websocket closed".to_string()),
        Message::Frame(_) => Ok(None),
    }
}

async fn send_envelope(write: &mut WsWrite, envelope: &NodeEnvelope) -> Result<(), String> {
    let text = serde_json::to_string(envelope)
        .map_err(|err| format!("failed to serialize envelope: {err}"))?;
    write
        .send(Message::Text(text.into()))
        .await
        .map_err(|err| format!("failed to send gateway envelope: {err}"))
}

async fn update_status<F>(
    status: &Arc<Mutex<GatewayNodeStatus>>,
    app: &AppHandle,
    update: F,
) -> GatewayNodeStatus
where
    F: FnOnce(&mut GatewayNodeStatus),
{
    let snapshot = {
        let mut status = status.lock().await;
        update(&mut status);
        status.clone()
    };
    emit_frontend_event(
        app,
        GatewayNodeFrontendEvent::StatusChanged {
            at: now_rfc3339(),
            status: snapshot.clone(),
        },
    );
    snapshot
}

fn emit_frontend_event(app: &AppHandle, event: GatewayNodeFrontendEvent) {
    let _ = app.emit(FRONTEND_EVENT, event);
}

fn resolve_config(
    config: Option<GatewayNodeConnectConfig>,
) -> Result<ResolvedConnectConfig, String> {
    let config = config.unwrap_or(GatewayNodeConnectConfig {
        url: None,
        token: None,
        node_id: None,
        display_name: None,
        default_session_id: None,
        metadata: None,
    });

    let url = first_non_empty(config.url, env::var("NAHIDA_GATEWAY_WS_URL").ok())
        .unwrap_or_else(|| DEFAULT_GATEWAY_WS_URL.to_string());
    let token = first_non_empty(config.token, env::var("NAHIDA_DESKTOP_NODE_TOKEN").ok())
        .or_else(|| env::var("NAHIDA_NODE_TOKEN").ok())
        .unwrap_or_default();
    if token.trim().is_empty() && !url_has_token(&url) {
        return Err(
            "node token is required; set NAHIDA_DESKTOP_NODE_TOKEN or pass config.token"
                .to_string(),
        );
    }

    let node_id = first_non_empty(config.node_id, env::var("NAHIDA_DESKTOP_NODE_ID").ok())
        .unwrap_or_else(|| DEFAULT_NODE_ID.to_string());
    let display_name = first_non_empty(config.display_name, None)
        .unwrap_or_else(|| DEFAULT_DISPLAY_NAME.to_string());
    let default_session_id = first_non_empty(
        config.default_session_id,
        env::var("NAHIDA_DESKTOP_SESSION_ID").ok(),
    );
    let mut metadata = config.metadata.unwrap_or_default();
    metadata
        .entry("platform".to_string())
        .or_insert_with(|| Value::String(env::consts::OS.to_string()));
    metadata
        .entry("app_version".to_string())
        .or_insert_with(|| Value::String(env!("CARGO_PKG_VERSION").to_string()));

    Ok(ResolvedConnectConfig {
        url,
        token,
        node_id,
        display_name,
        default_session_id,
        metadata,
    })
}

fn with_query_token(url: &str, token: &str) -> Result<String, String> {
    if token.trim().is_empty() {
        return Ok(url.to_string());
    }

    let mut parsed = Url::parse(url).map_err(|err| format!("gateway url is invalid: {err}"))?;
    let has_token = parsed
        .query_pairs()
        .any(|(key, _)| key == "token" || key == "node_token");
    if !has_token {
        parsed.query_pairs_mut().append_pair("token", token);
    }
    Ok(parsed.to_string())
}

fn url_has_token(url: &str) -> bool {
    Url::parse(url)
        .map(|parsed| {
            parsed
                .query_pairs()
                .any(|(key, value)| (key == "token" || key == "node_token") && !value.is_empty())
        })
        .unwrap_or(false)
}

fn first_non_empty(primary: Option<String>, fallback: Option<String>) -> Option<String> {
    primary
        .and_then(non_empty)
        .or_else(|| fallback.and_then(non_empty))
}

fn non_empty(value: String) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

fn env_or(key: &str, fallback: &str) -> String {
    env::var(key)
        .ok()
        .and_then(non_empty)
        .unwrap_or_else(|| fallback.to_string())
}

fn now_rfc3339() -> String {
    OffsetDateTime::now_utc()
        .format(&Rfc3339)
        .unwrap_or_else(|_| format!("{}", unix_millis()))
}
