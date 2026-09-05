# Orchestrator Alerts

Engine health-check alerts from the 15-min scheduled monitor.

---

[2026-08-12 15:48:04 UTC] ENGINE HEALTH FAIL: /health unreachable — curl to https://apiwitchcraft.duckdns.org/health returned HTTP 000 (connection timeout, exit 28) on all attempts; DNS resolves to 92.4.66.192 but no TCP/TLS response. Spot-check /vat/DE also failed with HTTP 000. Service appears down / not accepting connections. Could not verify status:"ok" or endpoints array length (==20). propose REVERT (do NOT deploy)

---

[2026-09-05 04:20:50 UTC] ENGINE HEALTH FAIL: /health unreachable — curl to https://apiwitchcraft.duckdns.org/health returned HTTP 000 (connection timeout, curl exit 28) after 45s on all attempts; DNS (apiwitchcraft.duckdns.org) resolves to 137.23.51.230 but no TCP/TLS response on :443 or :80 (both time out). Spot-check /vat/DE also returned HTTP 000 (exit 28). Did not verify "status":"ok" nor endpoints array length (==20). Service appears down / not accepting connections. propose REVERT (do NOT deploy)
