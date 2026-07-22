use keyring::v1::{Entry, Error as KeyringError};
use serde::{Deserialize, Serialize};

const SERVICE: &str = "top.cobaltdev.nahida.desktop";
const NODE_TOKEN_ACCOUNT: &str = "gateway-node-token";
const ADMIN_TOKEN_ACCOUNT: &str = "gateway-admin-bearer-token";
const MAXIMUM_TOKEN_LENGTH: usize = 512;

#[derive(Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SecureTokens {
    node_token: String,
    admin_bearer_token: String,
}

fn entry(account: &str) -> Result<Entry, String> {
    Entry::new(SERVICE, account).map_err(|error| format!("credential store unavailable: {error}"))
}

fn read_token(account: &str) -> Result<String, String> {
    match entry(account)?.get_password() {
        Ok(value) => Ok(value),
        Err(KeyringError::NoEntry) => Ok(String::new()),
        Err(error) => Err(format!("could not read credential: {error}")),
    }
}

fn write_token(account: &str, value: &str) -> Result<(), String> {
    let credential = entry(account)?;
    if value.is_empty() {
        return match credential.delete_credential() {
            Ok(()) | Err(KeyringError::NoEntry) => Ok(()),
            Err(error) => Err(format!("could not delete credential: {error}")),
        };
    }
    if value.len() > MAXIMUM_TOKEN_LENGTH {
        return Err("credential exceeds the supported length".to_owned());
    }
    credential
        .set_password(value)
        .map_err(|error| format!("could not save credential: {error}"))
}

#[tauri::command]
pub fn secure_tokens_read() -> Result<SecureTokens, String> {
    Ok(SecureTokens {
        node_token: read_token(NODE_TOKEN_ACCOUNT)?,
        admin_bearer_token: read_token(ADMIN_TOKEN_ACCOUNT)?,
    })
}

#[tauri::command]
pub fn secure_tokens_write(tokens: SecureTokens) -> Result<(), String> {
    write_token(NODE_TOKEN_ACCOUNT, tokens.node_token.trim())?;
    write_token(ADMIN_TOKEN_ACCOUNT, tokens.admin_bearer_token.trim())
}
