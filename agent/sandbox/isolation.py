"""
Isolation helpers â€” resource constraints and security hardening
applied to every sandbox container.

This module produces the kwargs dict passed to docker.containers.run()
so that DockerSandbox and any other caller share a single source of truth
for hardening policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Isolation profile
# ---------------------------------------------------------------------------


@dataclass
class IsolationProfile:
    """
    Encapsulates all Docker security / resource settings for one container.

    Profiles:
      - strict   (default) â€” no network, minimal caps, tightest limits
      - standard â€” outbound HTTP allowed (e.g. for pip install inside sandbox)
      - minimal  â€” for local dev / CI where full hardening isn't needed
    """

    # Resource limits
    cpu_limit: float = 1.0          # CPU cores
    memory_limit: str = "512m"      # Docker mem_limit string
    pids_limit: int = 64
    storage_limit_mb: int = 512     # ulimit -f equivalent (set via entrypoint)

    # Network
    network_mode: str = "none"      # "none" | "bridge"

    # Security options
    no_new_privileges: bool = True
    cap_drop: list[str] = field(default_factory=lambda: ["ALL"])
    cap_add: list[str] = field(default_factory=list)   # e.g. ["NET_BIND_SERVICE"]
    read_only_root: bool = False     # True breaks pip/apt; use with care
    seccomp_profile_path: str | None = None

    # Tmpfs mounts (ephemeral RAM disk)
    tmpfs: dict[str, str] = field(default_factory=lambda: {
        "/tmp": "size=64m,mode=1777"
    })

    def to_docker_kwargs(self) -> dict[str, Any]:
        """Return kwargs suitable for docker-py containers.run()."""
        kwargs: dict[str, Any] = {
            "cpu_period": 100_000,
            "cpu_quota": int(self.cpu_limit * 100_000),
            "mem_limit": self.memory_limit,
            "pids_limit": self.pids_limit,
            "network_mode": self.network_mode,
            "cap_drop": self.cap_drop,
            "read_only": self.read_only_root,
            "tmpfs": self.tmpfs,
        }

        # security_opt list
        sec_opts: list[str] = []
        if self.no_new_privileges:
            sec_opts.append("no-new-privileges:true")
        if self.seccomp_profile_path:
            import json
            from pathlib import Path
            profile_path = Path(self.seccomp_profile_path)
            if profile_path.exists():
                with open(profile_path) as f:
                    profile = json.load(f)
                sec_opts.append(f"seccomp={json.dumps(profile)}")
        if sec_opts:
            kwargs["security_opt"] = sec_opts

        if self.cap_add:
            kwargs["cap_add"] = self.cap_add

        return kwargs


# ---------------------------------------------------------------------------
# Pre-built profiles
# ---------------------------------------------------------------------------


def strict_profile(
    cpu: float = 1.0,
    memory: str = "512m",
    seccomp_path: str | None = "security/seccomp_profile.json",
) -> IsolationProfile:
    """
    Strictest isolation â€” no network, all caps dropped, seccomp whitelist.
    Use this for running untrusted code snippets.
    """
    return IsolationProfile(
        cpu_limit=cpu,
        memory_limit=memory,
        pids_limit=64,
        network_mode="none",
        no_new_privileges=True,
        cap_drop=["ALL"],
        read_only_root=False,
        seccomp_profile_path=seccomp_path,
    )


def standard_profile(
    cpu: float = 1.0,
    memory: str = "1g",
) -> IsolationProfile:
    """
    Standard isolation â€” allows outbound HTTP for pip installs etc.
    Still drops all capabilities and disallows privilege escalation.
    """
    return IsolationProfile(
        cpu_limit=cpu,
        memory_limit=memory,
        pids_limit=128,
        network_mode="bridge",
        no_new_privileges=True,
        cap_drop=["ALL"],
        read_only_root=False,
        seccomp_profile_path=None,
    )


def minimal_profile() -> IsolationProfile:
    """
    Minimal isolation for local dev / CI only.
    Do NOT use in production.
    """
    return IsolationProfile(
        cpu_limit=2.0,
        memory_limit="2g",
        pids_limit=256,
        network_mode="bridge",
        no_new_privileges=False,
        cap_drop=[],
        read_only_root=False,
        seccomp_profile_path=None,
    )
