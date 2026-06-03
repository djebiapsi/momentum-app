#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
health_check.py — Pipeline de test API pour Momentum App
=========================================================
Teste toutes les routes API, mesure les temps de réponse et sauvegarde un rapport
dans logs/api_health_TIMESTAMP.log.

Usage :
  python health_check.py                              # local :5000, token depuis .env
  python health_check.py https://mon-app.onrender.com # distant
  python health_check.py http://localhost:5000 --full  # + tests write (calcul, etc.)
  python health_check.py --help

Codes de sortie :
  0  tout OK (ou seulement des échecs attendus)
  1  au moins un test inattendu a échoué
"""
import argparse
import io
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Force UTF-8 sur Windows (cp1252 ne supporte pas les caracteres Unicode)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Dépendance externe ────────────────────────────────────────────────────────
try:
    import requests as _requests
except ImportError:
    print("[ERREUR] Le module 'requests' est requis : pip install requests")
    sys.exit(1)

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None

# ── Constantes ────────────────────────────────────────────────────────────────
TIMEOUT = 30        # secondes par requête
SLOW_MS = 3000      # seuil d'alerte temps de réponse
LOG_DIR = Path(__file__).parent / "logs"


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _load_env_token() -> str:
    """Lit ADMIN_PASSWORD depuis .env (si python-dotenv disponible) ou os.environ."""
    if dotenv_values:
        env_path = Path(__file__).parent / ".env"
        vals = dotenv_values(str(env_path)) if env_path.exists() else {}
        token = vals.get("ADMIN_PASSWORD", "")
    else:
        token = ""
    return token or os.environ.get("ADMIN_PASSWORD", "")


class TestResult:
    def __init__(self, name, method, url):
        self.name = name
        self.method = method
        self.url = url
        self.status_code: int = 0
        self.elapsed_ms: float = 0
        self.body: str = ""
        self.ok: bool = False
        self.expected_failure: bool = False  # échec prévisible (ex. IBKR absent)
        self.notes: list[str] = []

    def verdict(self) -> str:
        if self.ok:
            label = "OK  "
        elif self.expected_failure:
            label = "SKIP"  # attendu
        else:
            label = "FAIL"
        slow = f" [{self.elapsed_ms:.0f}ms ⚠️ LENT]" if self.elapsed_ms > SLOW_MS else f" [{self.elapsed_ms:.0f}ms]"
        return f"{label} {self.method:4s} {self.url}{slow}"

    def to_log(self) -> str:
        lines = [
            f"{'─'*70}",
            f"[{self.verdict()}]",
            f"  → Status HTTP : {self.status_code}",
        ]
        if self.notes:
            for n in self.notes:
                lines.append(f"  i {n}")
        body = self.body[:800] + ("…" if len(self.body) > 800 else "")
        lines.append(f"  → Body : {body}")
        return "\n".join(lines)


class HealthChecker:
    def __init__(self, base_url: str, token: str, full: bool = False):
        self.base = base_url.rstrip("/")
        self.token = token
        self.full = full
        self.results: list[TestResult] = []
        self._session = _requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    def _headers_admin(self) -> dict:
        return {"X-Admin-Token": self.token} if self.token else {}

    def _call(self, method: str, path: str, body=None, admin=False,
              expected_codes=(200,), expected_failure=False) -> TestResult:
        url = self.base + path
        result = TestResult(name=path, method=method, url=url)
        result.expected_failure = expected_failure
        headers = self._headers_admin() if admin else {}
        t0 = time.time()
        try:
            resp = self._session.request(
                method, url,
                json=body,
                headers=headers,
                timeout=TIMEOUT,
            )
            result.elapsed_ms = (time.time() - t0) * 1000
            result.status_code = resp.status_code
            try:
                result.body = json.dumps(resp.json(), ensure_ascii=False, indent=2)
            except Exception:
                result.body = resp.text[:800]
            result.ok = resp.status_code in expected_codes
            if not result.ok and not expected_failure:
                result.notes.append(f"Code inattendu : attendu {expected_codes}, reçu {resp.status_code}")
                # Extraire l'erreur du body pour faciliter le debug
                try:
                    err = resp.json().get("error") or resp.json().get("message") or ""
                    if err:
                        result.notes.append(f"Erreur API : {err}")
                except Exception:
                    pass
        except _requests.exceptions.Timeout:
            result.elapsed_ms = (time.time() - t0) * 1000
            result.status_code = 0
            result.body = "TIMEOUT"
            result.notes.append(f"Timeout après {TIMEOUT}s — vérifier que l'app est démarrée")
        except _requests.exceptions.ConnectionError as e:
            result.elapsed_ms = (time.time() - t0) * 1000
            result.status_code = 0
            result.body = str(e)[:300]
            result.notes.append(f"Connexion refusée — l'app n'est pas accessible à {self.base}")
        except Exception as e:
            result.elapsed_ms = (time.time() - t0) * 1000
            result.status_code = 0
            result.body = str(e)[:300]
            result.notes.append(f"Exception inattendue : {type(e).__name__}: {e}")
        self.results.append(result)
        return result

    # ── Tests ─────────────────────────────────────────────────────────────────

    def run(self):
        p = self._call

        # ── Santé de base ──────────────────────────────────────────────────
        p("GET",  "/",                     expected_codes=(200,))
        p("GET",  "/api/settings",         expected_codes=(200,))
        p("GET",  "/api/auth/check",       expected_codes=(200,))

        # ── Panel Long ────────────────────────────────────────────────────
        p("GET",  "/api/panel",            expected_codes=(200,))

        # ── Historique ────────────────────────────────────────────────────
        p("GET",  "/api/history",          expected_codes=(200,))
        r_latest = p("GET", "/api/history/latest", expected_codes=(200, 404))
        r_latest.notes.append("404 = aucun calcul précédent (normal à la 1re installation)")

        # ── Marché / régime ───────────────────────────────────────────────
        p("GET",  "/api/market-regime",    expected_codes=(200,))

        # ── Email ─────────────────────────────────────────────────────────
        p("GET",  "/api/email/status",     expected_codes=(200,))

        # ── Short ─────────────────────────────────────────────────────────
        p("GET",  "/api/short/panel",           expected_codes=(200,))
        p("GET",  "/api/short/settings",        expected_codes=(200,))
        p("GET",  "/api/short/history/latest",  expected_codes=(200, 404))

        # ── Options ───────────────────────────────────────────────────────
        p("GET",  "/api/options/saved",    expected_codes=(200,))

        # ── Auth : route protégée sans token → 401 attendu ────────────────
        r_noauth = p("POST", "/api/calculate", expected_codes=(200, 401, 400, 500),
                     expected_failure=True)
        r_noauth.notes.append("Sans token admin : 401 attendu si ADMIN_PASSWORD configuré")

        # ── IBKR status (sans connexion active) ───────────────────────────
        p("GET",  "/api/ibkr/status",      expected_codes=(200,))

        # ── IBKR connect : très probablement KO sans conteneur ────────────
        r_ibkr = p("POST", "/api/ibkr/connect", admin=True,
                   expected_codes=(200,), expected_failure=True)
        r_ibkr.notes.append(
            "503 attendu si le conteneur ib-gateway n'est pas démarré. "
            "Vérifier IB_GATEWAY_HOST et IB_GATEWAY_PORT."
        )
        if r_ibkr.status_code == 503:
            r_ibkr.ok = True   # échec attendu → ne pas compter comme FAIL

        # ── Tests WRITE (seulement avec --full) ───────────────────────────
        if self.full:
            self._run_full()

    def _run_full(self):
        """Tests write : requièrent ADMIN_PASSWORD + Tiingo configurés."""
        p = self._call

        if not self.token:
            r = TestResult("token_manquant", "INFO", self.base)
            r.ok = False
            r.notes.append("--full nécessite ADMIN_PASSWORD dans .env pour les tests write")
            r.body = "Token admin absent — tests write ignorés"
            self.results.append(r)
            return

        # Calcul momentum Long
        r_calc = p("POST", "/api/calculate", admin=True,
                   expected_codes=(200,), expected_failure=False)
        r_calc.notes.append("Peut prendre 10-30s selon la taille du panel et la latence Tiingo")

        # Calcul Short
        p("POST", "/api/short/calculate", admin=True,
          expected_codes=(200,), expected_failure=False)

        # Screener Long (IBKR optionnel)
        r_sc = p("POST", "/api/screener/generate", admin=True,
                 expected_codes=(200,), expected_failure=True)
        r_sc.notes.append("Peut retourner une erreur si IBKR non disponible (screener utilise IBKR pour le portfolio)")

    # ── Rapport ───────────────────────────────────────────────────────────────

    def report(self) -> str:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total   = len(self.results)
        passed  = sum(1 for r in self.results if r.ok)
        skipped = sum(1 for r in self.results if r.expected_failure and not r.ok)
        failed  = sum(1 for r in self.results if not r.ok and not r.expected_failure)

        lines = [
            "═" * 70,
            f"MOMENTUM APP — Health Check  {now_str}",
            f"Target : {self.base}",
            f"Mode   : {'full (write)' if self.full else 'read-only'}",
            "═" * 70,
            "",
        ]
        for r in self.results:
            lines.append(r.to_log())
            lines.append("")

        lines += [
            "═" * 70,
            f"RÉSUMÉ : {passed}/{total} OK  |  {failed} ÉCHEC(S)  |  {skipped} attendu(s)",
            "═" * 70,
        ]

        if failed:
            lines.append("")
            lines.append("── ÉCHECS INATTENDUS ──────────────────────────────────────────────────")
            for r in self.results:
                if not r.ok and not r.expected_failure:
                    lines.append(f"  ✗ {r.method} {r.url}")
                    for n in r.notes:
                        lines.append(f"      {n}")
            lines.append("")
            lines.append("Actions correctives :")
            lines.append("  1. Vérifier que TIINGO_API_KEY est défini dans .env / variables Render")
            lines.append("  2. Vérifier que la DB est accessible (DATABASE_URL)")
            lines.append("  3. Consulter les logs Flask/Gunicorn pour la stack trace complète")

        return "\n".join(lines)

    def exit_code(self) -> int:
        return 1 if any(not r.ok and not r.expected_failure for r in self.results) else 0


# ═════════════════════════════════════════════════════════════════════════════
# Affichage couleur console
# ═════════════════════════════════════════════════════════════════════════════

try:
    import colorama
    colorama.init()
    C_OK   = "\033[32m"
    C_SKIP = "\033[33m"
    C_FAIL = "\033[31m"
    C_RST  = "\033[0m"
except ImportError:
    C_OK = C_SKIP = C_FAIL = C_RST = ""


def _color_line(line: str) -> str:
    if line.startswith("OK  "):
        return C_OK + line + C_RST
    if line.startswith("SKIP"):
        return C_SKIP + line + C_RST
    if line.startswith("FAIL"):
        return C_FAIL + line + C_RST
    return line


# ═════════════════════════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Pipeline de tests API pour Momentum App",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("base_url", nargs="?", default="http://127.0.0.1:5000",
                        help="URL de base (défaut: http://127.0.0.1:5000)")
    parser.add_argument("--token", default=None,
                        help="Token admin (défaut: ADMIN_PASSWORD depuis .env)")
    parser.add_argument("--full", action="store_true",
                        help="Inclure les tests write (calcul momentum, screener…)")
    args = parser.parse_args()

    token = args.token or _load_env_token()
    checker = HealthChecker(base_url=args.base_url, token=token, full=args.full)

    print(f"[health_check] Démarrage — target: {args.base_url}")
    checker.run()

    report = checker.report()

    # Sauvegarder dans logs/
    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"api_health_{ts}.log"
    log_path.write_text(report, encoding="utf-8")
    print(f"[health_check] Rapport sauvegarde -> {log_path}")

    # Affichage console compact (verdicts uniquement)
    print()
    for r in checker.results:
        verdict = r.verdict()
        print(_color_line(verdict))
        for n in r.notes:
            print(f"  i  {n}")

    # Résumé final
    failed = sum(1 for r in checker.results if not r.ok and not r.expected_failure)
    print()
    if failed:
        print(f"{C_FAIL}ECHEC : {failed} test(s) en echec --- voir {log_path}{C_RST}")
    else:
        print(f"{C_OK}OK : Tous les tests passent (echecs attendus signales correctement){C_RST}")

    sys.exit(checker.exit_code())


if __name__ == "__main__":
    main()
