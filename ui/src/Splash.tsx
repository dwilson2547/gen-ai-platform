import { useState } from "react";
import { api, type Config } from "./api.ts";

const ERRORS: Record<string, string> = {
  not_member: "Your account isn't on this platform yet. Ask Daniel to add you.",
};

export function Splash({ config, onSignedIn }: { config: Config | null; onSignedIn: () => void }) {
  const code = new URLSearchParams(location.search).get("signin_error");
  const error = code ? ERRORS[code] ?? "Sign-in didn't work. Try again." : null;
  const [name, setName] = useState("");
  const [devError, setDevError] = useState<string | null>(null);

  const dev = async () => {
    try { await api.devLogin(name || "dev"); history.replaceState(null, "", "/"); onSignedIn(); }
    catch (e) { setDevError(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div className="splash">
      <div className="splash-card">
        <div className="splash-art" aria-hidden>
          {Array.from({ length: 16 }, (_, i) => <span key={i} style={{ animationDelay: `${(i * 137) % 1600}ms` }} />)}
        </div>
        <h1>genai</h1>
        <p className="muted">Describe it, get images back in seconds, keep them by project with the recipe that made
          them. Sprites, concept art, whatever your game needs.</p>
        {error && <p className="error" role="alert">{error}</p>}
        {config === null ? <p className="error">Can't reach the server.</p>
          : config.oidc ? <a className="button primary big" href="/api/auth/oidc/login">Sign in</a>
          : !config.dev_login && <p className="error">Sign-in isn't set up on this server.</p>}
        {config?.dev_login && (
          <form className="dev" onSubmit={e => { e.preventDefault(); dev(); }}>
            <span className="muted small">Dev sign-in</span>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="name" aria-label="dev user name" />
            <button type="submit">Enter</button>
            {devError && <span className="error small">{devError}</span>}
          </form>
        )}
      </div>
    </div>
  );
}
