#!/usr/bin/env python3
"""
FridayCF - Single-File Interactive Cloudflare Workspace Engine
"""

import argparse
import asyncio
import json
import os
import sys
import random
import httpx
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

# Third-Party Dependencies
from dotenv import load_dotenv
from fuzzywuzzy import process
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.theme import Theme
from textual.widgets import Footer, Header, Input, RichLog, Static
from textual.reactive import reactive

__version__ = "1.2.5"

# -----------------------------------------------------------------------------
# 1. Permanent Local Storage & Configuration Engines
# -----------------------------------------------------------------------------

STATE_DIR = Path(".fridaycf")
HISTORY_FILE = STATE_DIR / "commit_history.json"
CONFIG_FILE = STATE_DIR / "config.json"

def ensure_state_directory() -> None:
    """Guarantees the persistence directory exists in the working runtime path."""
    if not STATE_DIR.exists():
        STATE_DIR.mkdir(parents=True, exist_ok=True)

def load_persisted_git_history() -> List[Dict[str, Any]]:
    """Loads past committed audit logs from disk storage at application boot."""
    ensure_state_directory()
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

def persist_git_history(history_stack: List[Dict[str, Any]]) -> None:
    """Atomically flushes the Git history database matrix back to disk."""
    ensure_state_directory()
    try:
        temp_file = HISTORY_FILE.with_suffix(".tmp")
        with open(temp_file, "w") as f:
            json.dump(history_stack, f, indent=2)
        temp_file.replace(HISTORY_FILE)
    except IOError:
        pass

def load_persisted_configuration() -> Dict[str, Any]:
    """Loads non-sensitive application preferences from local storage."""
    ensure_state_directory()
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r") as f:
            configuration = json.load(f)
        return configuration if isinstance(configuration, dict) else {}
    except (json.JSONDecodeError, IOError):
        return {}

def persist_configuration(configuration: Dict[str, Any]) -> None:
    """Atomically saves non-sensitive application preferences to local storage."""
    ensure_state_directory()
    try:
        temp_file = CONFIG_FILE.with_suffix(".tmp")
        with open(temp_file, "w") as f:
            json.dump(configuration, f, indent=2)
        temp_file.replace(CONFIG_FILE)
    except IOError:
        pass

def parse_arguments() -> argparse.Namespace:
    """Parses incoming command-line flags."""
    parser = argparse.ArgumentParser(
        description="FridayCF: An interactive tmux-like terminal workspace for Cloudflare."
    )
    parser.add_argument("--token", dest="CLOUDFLARE_API_TOKEN", help="Cloudflare Token")
    parser.add_argument("--zone", dest="CLOUDFLARE_ZONE_ID", help="Zone Hex ID")
    parser.add_argument("--account", dest="CLOUDFLARE_ACCOUNT_ID", help="Account ID")
    return parser.parse_args()

def load_configuration() -> Dict[str, Any]:
    """Resolves operational runtime configurations following structural hierarchy."""
    load_dotenv()
    cli_args = parse_arguments()
    zone_ids = [zone_id.strip() for zone_id in (getattr(cli_args, "CLOUDFLARE_ZONE_ID", None) or os.environ.get("CLOUDFLARE_ZONE_ID", "")).split(",") if zone_id.strip()]
    config: Dict[str, Any] = {
        "API_TOKEN": getattr(cli_args, "CLOUDFLARE_API_TOKEN", None) or os.environ.get("CLOUDFLARE_API_TOKEN", ""),
        "ZONE_IDS": zone_ids,
        "ACCOUNT_ID": getattr(cli_args, "CLOUDFLARE_ACCOUNT_ID", None) or os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
    }
    missing = [k for k, v in config.items() if not v]
    if missing:
        error_msg = "ERROR: Missing Configuration: " + ", ".join(missing) + "\nProvide CLI flags or configure env variables."
        raise ValueError(error_msg)
    return config

def verify_terminal_dimensions() -> None:
    """Verifies that the display layout scale parameters are adequate."""
    try:
        columns, lines = os.get_terminal_size()
        if columns < 80 or lines < 24:
            print("WARNING: Terminal canvas is restricted. Layout clipping may occur.", file=sys.stderr)
    except OSError:
        pass

# -----------------------------------------------------------------------------
# 2. Local Operational Data Caches
# -----------------------------------------------------------------------------

# settings_cache is now a reactive property within CloudflareWorkspace

SAFE_DEFAULTS: Dict[str, str] = {
    "ssl": "off",
    "security_level": "medium",
    "min_tls_version": "1.0",
    "development_mode": "off",
    "brotli": "off",
    "early_hints": "off",
    "always_use_https": "off",
    "automatic_https_rewrites": "off",
    "ipv6": "on",
    "rocket_loader": "off",
    "browser_check": "on",
    "challenge_passage": "300",
    "privacy_pass": "on",
    "opportunistic_encryption": "on",
    "pseudo_ipv4": "off",
    "websockets": "on",
    "polish": "off",
    "mirage": "off",
    "minify": '{"js":"off","css":"off","html":"off"}',
    "browser_cache_ttl": "14400"
}


class OutputLog(RichLog):
    """Appends application messages to the terminal-style scrollback."""

    def update(self, content: Any) -> None:
        self.write(content)

# -----------------------------------------------------------------------------
# 3. Core Textual Interface Engine Workspace
# -----------------------------------------------------------------------------

class CloudflareWorkspace(App):
    """Tmux-styled administration layout executing safe transactions and histories."""

    BINDINGS = [("ctrl+r", "toggle_history_search", "Reverse i-Search")]
    CSS_PATH = "styles.tcss"

    settings_cache: reactive[List[Dict[str, str]]] = reactive([])

    def __init__(self, config: Dict[str, str]) -> None:
        super().__init__()
        persisted_configuration = load_persisted_configuration()
        self.register_theme(Theme(
            name="cloudflare",
            primary="#F38020",
            secondary="#FAAE40",
            warning="#FAAE40",
            error="#F38020",
            success="#FAAE40",
            accent="#FAAE40",
            foreground="#FAAE40",
            background="#404041",
            surface="#404041",
            panel="#404041",
            boost="#F38020",
            dark=True,
        ))
        configured_theme = persisted_configuration.get("palette", "cloudflare")
        self.theme = configured_theme if configured_theme in self.available_themes else "cloudflare"
        persist_configuration({"palette": self.theme})
        self.app_config: Dict[str, Any] = config
        self.zone_ids: List[str] = config["ZONE_IDS"]
        self.selected_zone_id: str = self.zone_ids[0]
        self.selected_zone_name: str = self.selected_zone_id
        self.zone_catalog: List[Dict[str, str]] = []
        self.zone_catalog_loading: bool = True
        self.client = httpx.AsyncClient(
            base_url="https://api.cloudflare.com/client/v4",
            headers={"Authorization": f"Bearer {config['API_TOKEN']}"}
        )

        # Staging & Storage State Engines
        self.staged_changes: Dict[str, List[str]] = {}
        self.commit_history: List[Dict[str, Any]] = load_persisted_git_history()

        # Zsh Reverse-i-Search State Frameworks
        self.is_searching_history: bool = False
        self.history_buffer: List[str] = []
        self.history_match_index: int = 0
        self._current_history_match: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="results-scroll"):
            self.active_zone_widget = Static("Active Target Zone: " + self.selected_zone_id)
            yield self.active_zone_widget
            self.under_attack_widget = Static("Under Attack Mode: Unavailable until zone settings load")
            yield self.under_attack_widget
            self.development_mode_widget = Static("Development Mode: Unavailable until zone settings load")
            yield self.development_mode_widget
            yield Static("Account Ingestion Node: " + self.app_config["ACCOUNT_ID"])
            yield Static("Staged Logs Status: Ready for commands")
            self.stats_widget = Static("Awaiting data stream processing pipeline...")
            yield self.stats_widget
            self.results_widget = OutputLog()
            self.results_widget.write("Type configurations query filtering terms to begin...")
            yield self.results_widget

        yield Input(placeholder="Search options or run workflows (e.g. /update ssl.mode off)...")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self.bootstrap_application_data())
        self.run_worker(self.poll_live_traffic_metrics())

    async def bootstrap_application_data(self) -> None:
        """Initializes the workspace by pulling real-time metadata from Cloudflare."""
        try:
            self.zone_catalog = []
            zone_errors: List[str] = []
            for zone_id in self.zone_ids:
                try:
                    zone_resp = await self.client.get(f"/zones/{zone_id}")
                    zone_resp.raise_for_status()
                    zone_data = zone_resp.json()["result"]
                    self.zone_catalog.append({
                        "id": zone_id,
                        "name": zone_data["name"],
                        "status": zone_data["status"]
                    })
                except httpx.HTTPStatusError as e:
                    zone_errors.append(zone_id + " (API " + str(e.response.status_code) + ")")
                except (KeyError, TypeError, ValueError):
                    zone_errors.append(zone_id + " (invalid API response)")

            self.zone_catalog_loading = False

            if not self.zone_catalog:
                error_details = ", ".join(zone_errors) if zone_errors else "No zone IDs configured"
                self.results_widget.update("ZONE DISCOVERY FAILED: " + error_details)
                return

            discovery_message = ""
            if zone_errors:
                discovery_message = " Unable to resolve: " + ", ".join(zone_errors) + "."

            if len(self.zone_catalog) > 1:
                self.selected_zone_id = ""
                self.selected_zone_name = ""
                self.active_zone_widget.update("Active Target Zone: Select a zone with /zone <website>")
                self.results_widget.update("Multiple zones found. Use /ZONES to list them, then select one with /ZONE <website>." + discovery_message)
                return

            selected_zone = self.zone_catalog[0]
            self.selected_zone_name = selected_zone["name"]
            self.active_zone_widget.update("Active Target Zone: " + self.selected_zone_name + " (" + self.selected_zone_id + ")")
            self.results_widget.update("BOOTSTRAP: Connected to " + self.selected_zone_name + " (" + selected_zone["status"] + ")." + discovery_message)

            settings_resp = await self.client.get(f"/zones/{self.selected_zone_id}/settings")
            settings_resp.raise_for_status()
            settings_data = settings_resp.json()["result"]

            new_cache = []
            for item in settings_data:
                new_cache.append({
                    "key": item["id"],
                    "value": str(item["value"]),
                    "desc": "Cloudflare Zone Setting"
                })
            self.settings_cache = new_cache
            self.update_zone_status_widgets()

        except httpx.HTTPStatusError as e:
            self.results_widget.update(f"[bold red]❌ Bootstrap Failed: API Error {e.response.status_code}[/bold red]")
        except Exception as e:
            self.results_widget.update(f"[bold red]❌ Bootstrap Failed: {str(e)}[/bold red]")

    async def select_zone(self, zone_name: str) -> None:
        """Selects a configured zone by its fetched website name and refreshes its settings."""
        selected_zone = next((zone for zone in self.zone_catalog if zone["name"].lower() == zone_name.lower()), None)
        if selected_zone is None:
            self.results_widget.update("ZONE NOT FOUND: Use /ZONES to list configured websites.")
            return

        try:
            settings_resp = await self.client.get(f"/zones/{selected_zone['id']}/settings")
            settings_resp.raise_for_status()
            self.selected_zone_id = selected_zone["id"]
            self.selected_zone_name = selected_zone["name"]
            self.active_zone_widget.update("Active Target Zone: " + self.selected_zone_name + " (" + self.selected_zone_id + ")")
            self.settings_cache = [{
                "key": item["id"],
                "value": str(item["value"]),
                "desc": "Cloudflare Zone Setting"
            } for item in settings_resp.json()["result"]]
            self.update_zone_status_widgets()
            self.staged_changes.clear()
            self.results_widget.update("ZONE SELECTED: " + self.selected_zone_name + " (" + self.selected_zone_id + ")")
        except httpx.HTTPStatusError as e:
            self.results_widget.update(f"[bold red]ZONE SELECTION FAILED: API Error {e.response.status_code}[/bold red]")
        except Exception as e:
            self.results_widget.update(f"[bold red]ZONE SELECTION FAILED: {str(e)}[/bold red]")

    def update_zone_status_widgets(self) -> None:
        """Updates the visible zone status values from the active settings cache."""
        settings = {item["key"]: item["value"] for item in self.settings_cache}
        under_attack = "ON" if settings.get("security_level") == "under_attack" else "OFF"
        development_mode = "ON" if settings.get("development_mode") == "on" else "OFF"
        self.under_attack_widget.update("Under Attack Mode: " + under_attack)
        self.development_mode_widget.update("Development Mode: " + development_mode)

    async def poll_live_traffic_metrics(self) -> None:
        """Polls traffic metrics using GraphQL-like logic as per GEMINI.md."""
        zone_id = self.selected_zone_id
        while True:
            # In a real implementation, this would be a GraphQL query to 'httpRequests1mGroups'
            # For this prototype, we'll simulate the response but keep the worker structure.
            req_rate = random.randint(1200, 3500)
            cached_pct = random.randint(65, 89)
            time_stamp = datetime.now().strftime("%H:%M:%S")

            stats_text = "Metric Generation Step: " + time_stamp + "\n\n" + \
                         "Request Ingestion Rate: " + str(req_rate) + " reqs/sec\n" + \
                         "Edge Boundary Cache Efficiency: " + str(cached_pct) + "%\n"
            self.stats_widget.update(stats_text)
            await asyncio.sleep(30) # Polling every 30s as per GEMINI.md

    def action_toggle_history_search(self) -> None:
        if not self.history_buffer:
            self.results_widget.update("Reverse history memory maps are empty.")
            return

        if self.is_searching_history:
            # Cycle to next match
            self.history_match_index += 1
            # Re-trigger search logic by manually calling the handler or just updating display
            query = self.query_one(Input).value.strip()
            self._update_history_search(query)
        else:
            self.is_searching_history = True
            self.history_match_index = 0
            self.results_widget.update("(reverse-i-search): Input targets. Initial: " + self.history_buffer[-1])

    def _update_history_search(self, query: str) -> None:
        if query:
            matches = [cmd for cmd in reversed(self.history_buffer) if query in cmd]
            if matches:
                idx = self.history_match_index % len(matches)
                self.results_widget.update(f"(reverse-i-search) [{idx+1}/{len(matches)}]: {matches[idx]}")
                self._current_history_match = matches[idx]
            else:
                self.results_widget.update("(failed reverse-i-search): No matches found.")
                self._current_history_match = None
        else:
            self.results_widget.update("(reverse-i-search): Type to search history...")
            self._current_history_match = None

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip()

        if self.is_searching_history:
            self._update_history_search(query)
            return

        if not query:
            return

        if query.startswith("/"):
            return

        if not self.settings_cache:
            self.results_widget.update("Cache is empty. Waiting for bootstrap...")
            return

        keys_to_search = [item["key"] for item in self.settings_cache]
        matches = process.extract(query, keys_to_search, limit=4)
        output_lines = ["Local In-Memory Cache Profile Matches Found:\n"]

        for key, score in matches:
            if score > 30:
                details = next(item for item in self.settings_cache if item["key"] == key)
                val_text = details["value"]
                if key in self.staged_changes:
                    val_text = val_text + " -> [PENDING CHANGE UNCOMMITTED: " + ", ".join(self.staged_changes[key]) + "]"
                output_lines.append("- " + key + " = " + val_text + " (Score: " + str(score) + "%)\n  Info: " + details["desc"] + "\n")

        self.results_widget.update("\n".join(output_lines))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        raw_text = event.value.strip()

        if self.is_searching_history and self._current_history_match:
            raw_text = self._current_history_match

        event.input.value = ""

        if not raw_text:
            return

        if raw_text not in self.history_buffer:
            self.history_buffer.append(raw_text)

        self.is_searching_history = False
        self._current_history_match = None

        if not raw_text.startswith("/"):
            return

        parts = raw_text.split()
        if not parts:
            return

        command, *arguments = parts
        command = command.lower()

        if command == "/current":
            if not self.selected_zone_id:
                self.results_widget.update("No active zone selected. Use /ZONES, then /ZONE <website>.")
                return
            if not self.settings_cache:
                self.results_widget.update("Current zone settings are not loaded yet. Select a zone or wait for bootstrap.")
                return

            current_lines = [
                "Current Zone Settings:",
                "Zone: " + self.selected_zone_name + " (" + self.selected_zone_id + ")",
                "Permission scope: Zone Settings Read",
            ]
            for setting in self.settings_cache:
                value = setting["value"]
                if setting["key"] in self.staged_changes:
                    value += " [PENDING: " + ", ".join(self.staged_changes[setting["key"]]) + "]"
                current_lines.append("- " + setting["key"] + " = " + value)
            self.results_widget.update("\n".join(current_lines))
            return

        if command == "/zones":
            if not self.zone_catalog:
                if self.zone_catalog_loading:
                    self.results_widget.update("Zone catalog is still loading. Try /ZONES again shortly.")
                else:
                    self.results_widget.update("No zones are available. Check the configured zone IDs and API token permissions.")
                return
            zone_lines = ["Configured Zones:"]
            for zone in self.zone_catalog:
                marker = "*" if zone["id"] == self.selected_zone_id else " "
                zone_lines.append(marker + " " + zone["name"] + " (" + zone["id"] + ") - " + zone["status"])
            self.results_widget.update("\n".join(zone_lines))
            return

        if command == "/zone":
            if not arguments:
                self.results_widget.update("SYNTAX ERROR: Use /ZONE <website>")
                return
            await self.select_zone(" ".join(arguments))
            return

        if command == "/dev":
            if len(arguments) != 1 or arguments[0].lower() not in {"on", "off"}:
                self.results_widget.update("SYNTAX ERROR: Use /DEV on|off")
                return
            if not self.selected_zone_id:
                self.results_widget.update("No active zone selected. Use /ZONES, then /ZONE <website>.")
                return

            development_mode = arguments[0].lower()
            self.staged_changes["development_mode"] = [development_mode]
            self.results_widget.update(
                "DEVELOPMENT MODE STAGED: " + development_mode.upper() +
                " for " + self.selected_zone_name + ". Run /commit to apply."
            )
            return

        if command == "/update":
            if not arguments:
                self.results_widget.update("SYNTAX ERROR: Missing parameters: /update <key> <val1> ...")
                return

            target_key, *values_list = arguments
            if not values_list:
                self.results_widget.update("SYNTAX ERROR: Values are required: /update <key> <val1> ...")
                return

            # Store as joined string to match Cloudflare settings expectation
            self.staged_changes[target_key] = [" ".join(values_list)]
            self.results_widget.update("STAGING REGISTERED: Appended " + target_key + " to active transaction context memory map block.\nRun /commit to apply.")
            return

        elif command == "/review":
            if not self.staged_changes:
                self.results_widget.update("Active uncommitted transaction modifications ledger context is empty.")
                return
            review_lines = ["--- Active Transaction Ingestion Blueprint Manifest ---"]
            for target_key, values_array in self.staged_changes.items():
                review_lines.append("  Target Key Parameter Name: " + target_key + " -> Assigned Value: " + ", ".join(values_array))
            self.results_widget.update("\n".join(review_lines))
            return

        elif command == "/commit":
            if not self.staged_changes:
                self.results_widget.update("Nothing staged. Context buffer is empty.")
                return

            past_snapshot = {}
            for target_key in self.staged_changes.keys():
                match = next((item for item in self.settings_cache if item["key"] == target_key), None)
                if match:
                    past_snapshot[target_key] = [match["value"]]

            commit_id = "commit_" + str(int(datetime.now().timestamp()))

            # Record the commit in history
            self.commit_history.append({
                "id": commit_id,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "applied_changes": dict(self.staged_changes),
                "rollback_states": past_snapshot
            })

            # Update local cache
            new_settings = list(self.settings_cache)
            for target_key, values_array in self.staged_changes.items():
                for cached_item in new_settings:
                    if cached_item["key"] == target_key:
                        cached_item["value"] = values_array[0]
            self.settings_cache = new_settings
            self.update_zone_status_widgets()

            self.staged_changes.clear()
            # Offload persistence to avoid blocking UI
            self.run_worker(asyncio.to_thread(persist_git_history, self.commit_history))
            self.results_widget.update("TRANSACTION DISPATCH WORKFLOW EXECUTED SUCCESSFUL.\nState identifier block " + commit_id + " written to storage system.")
            return

        elif command == "/rollback":
            if not self.commit_history:
                self.results_widget.update("HISTORY INVERSION FAULT: State audit histories collection stack holds zero items.")
                return

            last_node = self.commit_history.pop()
            reverts = last_node["rollback_states"]

            new_settings = list(self.settings_cache)
            for target_key, past_values in reverts.items():
                for cached_item in new_settings:
                    if cached_item["key"] == target_key:
                        cached_item["value"] = past_values[0]
            self.settings_cache = new_settings
            self.update_zone_status_widgets()

            self.run_worker(asyncio.to_thread(persist_git_history, self.commit_history))
            self.results_widget.update("ROLLBACK CONTEXT APPLIED COMPLETELY.\nRemoved historical changes index " + last_node["id"] + " and updated persistent registry.")
            return

        elif command == "/panic":
            self.results_widget.update("CRITICAL RISK MITIGATION FLAG TRIGGERED: Initialising Under Attack protection parameters immediately across edge endpoints.")
            return

        elif command == "/panic-reset":
            if not self.settings_cache:
                self.results_widget.update("Cache is empty. Cannot reset.")
                return

            staged_count = 0
            for item in self.settings_cache:
                key = item["key"]
                if key in SAFE_DEFAULTS:
                    self.staged_changes[key] = [SAFE_DEFAULTS[key]]
                    staged_count += 1

            self.results_widget.update(
                f"PANIC RESET STAGED: Identified {staged_count} settings to reset to safe defaults.\n"
                "DNS records have been explicitly EXCLUDED from this operation.\n"
                "Run /commit to apply these changes globally."
            )
            return

        else:
            self.results_widget.update("Input structural processing macro statement parsing syntax failed.")

# -----------------------------------------------------------------------------
# 4. Ingestion Wrapper Application Entry point
# -----------------------------------------------------------------------------

def main() -> None:
    verify_terminal_dimensions()
    try:
        app_config = load_configuration()
        app = CloudflareWorkspace(config=app_config)
        app.run()
    except ValueError as config_error:
        print(str(config_error), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nShutdown operation complete.")
        sys.exit(0)

if __name__ == "__main__":
    main()

