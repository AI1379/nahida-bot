use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

pub const PROTOCOL_VERSION: &str = "1.0";

static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(1);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EnvelopeKind {
    Request,
    Response,
    Event,
    Heartbeat,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CapabilityDirection {
    GatewayToNode,
    NodeToGateway,
    Bidirectional,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CapabilityRisk {
    Low,
    Medium,
    High,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct NodeErrorObject {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default, skip_serializing_if = "Map::is_empty")]
    pub details: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct NodeCapability {
    pub name: String,
    #[serde(default = "default_version")]
    pub version: String,
    #[serde(default = "default_direction")]
    pub direction: CapabilityDirection,
    #[serde(default = "default_risk")]
    pub risk: CapabilityRisk,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub description: String,
    #[serde(default, skip_serializing_if = "is_false")]
    pub requires_user_approval: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct NodeEnvelope {
    #[serde(default = "default_version")]
    pub version: String,
    pub kind: EnvelopeKind,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub method: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub event: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ok: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub payload: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<NodeErrorObject>,
    #[serde(default, skip_serializing_if = "Map::is_empty")]
    pub meta: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RegisterPayload {
    pub node_id: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub display_name: String,
    #[serde(default = "default_node_type")]
    pub node_type: String,
    #[serde(default)]
    pub capabilities: Vec<NodeCapability>,
    #[serde(default)]
    pub metadata: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RegisterOkPayload {
    #[serde(default = "default_true")]
    pub accepted: bool,
    pub session_id: String,
    #[serde(default = "default_heartbeat_interval")]
    pub heartbeat_interval_ms: u64,
    #[serde(default = "default_heartbeat_timeout")]
    pub heartbeat_timeout_ms: u64,
    #[serde(default)]
    pub server_time: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CapabilityInvokePayload {
    pub invoke_id: String,
    pub capability: String,
    #[serde(default)]
    pub arguments: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CapabilityCancelPayload {
    pub invoke_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HeartbeatPayload {
    #[serde(rename = "type")]
    pub heartbeat_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ts: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub echo_ts: Option<u64>,
}

pub fn build_request(method: &str, request_id: String, payload: Option<Value>) -> NodeEnvelope {
    NodeEnvelope {
        version: PROTOCOL_VERSION.to_string(),
        kind: EnvelopeKind::Request,
        id: Some(request_id),
        method: Some(method.to_string()),
        event: None,
        ok: None,
        payload,
        error: None,
        meta: Map::new(),
    }
}

pub fn build_response(
    request_id: String,
    ok: bool,
    payload: Option<Value>,
    error: Option<NodeErrorObject>,
) -> NodeEnvelope {
    NodeEnvelope {
        version: PROTOCOL_VERSION.to_string(),
        kind: EnvelopeKind::Response,
        id: Some(request_id),
        method: None,
        event: None,
        ok: Some(ok),
        payload: if ok { payload } else { None },
        error: if ok { None } else { error },
        meta: Map::new(),
    }
}

pub fn build_heartbeat(
    heartbeat_type: &str,
    ts: Option<u64>,
    echo_ts: Option<u64>,
) -> NodeEnvelope {
    let payload = serde_json::to_value(HeartbeatPayload {
        heartbeat_type: heartbeat_type.to_string(),
        ts,
        echo_ts,
    })
    .expect("heartbeat payload is serializable");

    NodeEnvelope {
        version: PROTOCOL_VERSION.to_string(),
        kind: EnvelopeKind::Heartbeat,
        id: None,
        method: None,
        event: None,
        ok: None,
        payload: Some(payload),
        error: None,
        meta: Map::new(),
    }
}

pub fn next_request_id(prefix: &str) -> String {
    let counter = REQUEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("req_{}_{}_{}", prefix, unix_millis(), counter)
}

pub fn unix_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

pub fn protocol_error(code: &str, message: impl Into<String>, retryable: bool) -> NodeErrorObject {
    NodeErrorObject {
        code: code.to_string(),
        message: message.into(),
        retryable,
        details: Map::new(),
    }
}

pub fn desktop_capabilities() -> Vec<NodeCapability> {
    vec![
        NodeCapability {
            name: "desktop.live2d.set_expression".to_string(),
            version: PROTOCOL_VERSION.to_string(),
            direction: CapabilityDirection::GatewayToNode,
            risk: CapabilityRisk::Low,
            description: "Set Live2D expression by expression id".to_string(),
            requires_user_approval: false,
        },
        NodeCapability {
            name: "desktop.live2d.play_motion".to_string(),
            version: PROTOCOL_VERSION.to_string(),
            direction: CapabilityDirection::GatewayToNode,
            risk: CapabilityRisk::Low,
            description: "Play Live2D motion by group and motion id".to_string(),
            requires_user_approval: false,
        },
        NodeCapability {
            name: "desktop.notification.show".to_string(),
            version: PROTOCOL_VERSION.to_string(),
            direction: CapabilityDirection::GatewayToNode,
            risk: CapabilityRisk::Low,
            description: "Show a native desktop notification".to_string(),
            requires_user_approval: false,
        },
        NodeCapability {
            name: "desktop.notification.announce".to_string(),
            version: PROTOCOL_VERSION.to_string(),
            direction: CapabilityDirection::GatewayToNode,
            risk: CapabilityRisk::Low,
            description: "Queue and speak a desktop reminder".to_string(),
            requires_user_approval: false,
        },
    ]
}

fn default_version() -> String {
    PROTOCOL_VERSION.to_string()
}

fn default_direction() -> CapabilityDirection {
    CapabilityDirection::GatewayToNode
}

fn default_risk() -> CapabilityRisk {
    CapabilityRisk::Low
}

fn default_node_type() -> String {
    "desktop".to_string()
}

fn default_true() -> bool {
    true
}

fn default_heartbeat_interval() -> u64 {
    15_000
}

fn default_heartbeat_timeout() -> u64 {
    45_000
}

fn is_false(value: &bool) -> bool {
    !*value
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn fixture_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join("tests")
            .join("fixtures")
            .join("gateway_node")
    }

    #[test]
    fn parses_gateway_node_fixtures() {
        for entry in fs::read_dir(fixture_dir()).expect("fixture dir exists") {
            let path = entry.expect("fixture entry").path();
            if path.extension().and_then(|value| value.to_str()) != Some("json") {
                continue;
            }
            let raw = fs::read_to_string(&path).expect("fixture is readable");
            let envelope: NodeEnvelope = serde_json::from_str(&raw)
                .unwrap_or_else(|err| panic!("{}: {err}", path.display()));
            assert_eq!(envelope.version, PROTOCOL_VERSION);
        }
    }

    #[test]
    fn round_trips_gateway_node_fixtures() {
        for entry in fs::read_dir(fixture_dir()).expect("fixture dir exists") {
            let path = entry.expect("fixture entry").path();
            if path.extension().and_then(|value| value.to_str()) != Some("json") {
                continue;
            }
            let raw = fs::read_to_string(&path).expect("fixture is readable");
            let mut source: Value = serde_json::from_str(&raw).expect("fixture json");
            source
                .as_object_mut()
                .expect("fixture object")
                .remove("_comment");

            let envelope: NodeEnvelope = serde_json::from_value(source.clone())
                .unwrap_or_else(|err| panic!("{}: {err}", path.display()));
            let dumped = serde_json::to_value(envelope).expect("envelope serializes");
            assert_eq!(dumped, source, "{}", path.display());
        }
    }

    #[test]
    fn registers_notification_announce_capability() {
        let capability = desktop_capabilities()
            .into_iter()
            .find(|capability| capability.name == "desktop.notification.announce")
            .expect("announce capability is registered");

        assert_eq!(capability.direction, CapabilityDirection::GatewayToNode);
        assert_eq!(capability.risk, CapabilityRisk::Low);
        assert!(!capability.requires_user_approval);
    }
}
