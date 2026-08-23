use std::fs;

use anyhow::{Context, Result, anyhow};
use figment::{
    Figment,
    providers::{Env, Format, Json},
};
use serde::{Deserialize, Serialize};

use crate::dirs::auth_file_path;

#[derive(Debug, Clone, Serialize, Deserialize)]
struct AuthPayload {
    email: String,
    password: String,
}

#[derive(Debug, Clone, Deserialize)]
struct AuthConfig {
    email: Option<String>,
    password: Option<String>,
}

fn load_auth_config(path: &std::path::Path) -> Result<AuthConfig> {
    let mut figment = Figment::new();
    if path.exists() {
        figment = figment.merge(Json::file(path));
    }
    figment
        .merge(Env::prefixed("THINGS3_"))
        .extract()
        .with_context(|| format!("Failed reading auth config at {}", path.display()))
}

fn validate_auth(email: &str, password: &str) -> Result<(String, String)> {
    let email = email.trim().to_string();
    let password = password.to_string();

    if email.is_empty() {
        return Err(anyhow!("Missing auth email."));
    }
    if password.is_empty() {
        return Err(anyhow!("Missing auth password."));
    }

    Ok((email, password))
}

pub fn load_auth() -> Result<(String, String)> {
    let path = auth_file_path();

    let cfg = load_auth_config(&path)?;

    let Some(email) = cfg.email else {
        return Err(anyhow!(
            "Missing auth email. Set THINGS3_EMAIL or run `things3 set-auth` to create {}.",
            path.display()
        ));
    };

    let Some(password) = cfg.password else {
        return Err(anyhow!(
            "Missing auth password. Set THINGS3_PASSWORD or run `things3 set-auth` to update {}.",
            path.display()
        ));
    };

    validate_auth(&email, &password)
}

pub fn write_auth(email: &str, password: &str) -> Result<std::path::PathBuf> {
    let (email, password) = validate_auth(email, password)?;
    let path = auth_file_path();
    let parent = path
        .parent()
        .ok_or_else(|| anyhow!("Invalid auth file path"))?
        .to_path_buf();
    fs::create_dir_all(&parent).with_context(|| format!("Failed creating {}", parent.display()))?;

    let payload = AuthPayload { email, password };
    let serialized = serde_json::to_string(&payload)?;

    // The staging file has to be private *before* the password reaches it.
    // Writing at the default mode and chmod'ing only after the rename left a
    // window where the plaintext password was world-readable, and the fixed
    // ".tmp" name let anyone able to write the directory pre-plant a symlink
    // for us to follow. Create it O_EXCL under a pid-unique name at 0600; the
    // rename then carries that mode onto the final file.
    let file_name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("auth.json");
    let tmp_path = parent.join(format!(".{}.{}.tmp", file_name, std::process::id()));
    let _ = fs::remove_file(&tmp_path);

    let mut opts = fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let staged = opts.open(&tmp_path).and_then(|mut f| {
        use std::io::Write;
        f.write_all(serialized.as_bytes())?;
        f.sync_all()
    });
    if let Err(err) = staged {
        let _ = fs::remove_file(&tmp_path);
        return Err(err).with_context(|| format!("Failed writing {}", tmp_path.display()));
    }

    if let Err(err) = fs::rename(&tmp_path, &path) {
        let _ = fs::remove_file(&tmp_path);
        return Err(err).with_context(|| format!("Failed finalizing {}", path.display()));
    }

    Ok(path)
}
