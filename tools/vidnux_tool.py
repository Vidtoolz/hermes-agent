#!/usr/bin/env python3
"""
Vidnux Tool Module - Local workstation diagnostics for Hermes.

Read-only tools for checking the user's Ubuntu / Resolve / NVIDIA workstation.
No sudo, no installs, no file writes, no destructive commands.
"""

import json
import os
import platform
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from tools.registry import registry


def _run_command(command: List[str], timeout: int = 8) -> Dict[str, Any]:
    """Run a safe read-only command and return structured output."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "command": " ".join(command),
            "available": True,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except FileNotFoundError:
        return {
            "command": " ".join(command),
            "available": False,
            "error": f"Command not found: {command[0]}",
        }
    except subprocess.TimeoutExpired:
        return {
            "command": " ".join(command),
            "available": True,
            "error": f"Command timed out after {timeout}s",
        }
    except Exception as exc:
        return {
            "command": " ".join(command),
            "available": False,
            "error": str(exc),
        }


def _read_os_release() -> Dict[str, str]:
    """Read /etc/os-release without shelling out."""
    data: Dict[str, str] = {}
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as f:
            for line in f:
                if "=" not in line:
                    continue
                key, value = line.rstrip("\n").split("=", 1)
                data[key] = value.strip().strip('"')
    except Exception as exc:
        data["error"] = str(exc)
    return data


def _basic_paths() -> Dict[str, Optional[str]]:
    """Report paths to common tools used on Vidnux."""
    commands = [
        "hermes",
        "git",
        "python3",
        "nvidia-smi",
        "nvcc",
        "docker",
        "rg",
        "node",
    ]
    return {cmd: shutil.which(cmd) for cmd in commands}


def vidnux_status(*args, **kwargs) -> str:
    """
    Return read-only diagnostic information about the Vidnux workstation.

    Accepts both direct keyword arguments and Hermes registry dispatch style:
    handler(args_dict, task_id=..., user_task=...).

    Args:
        include_network: Include network interface summary.
        include_disks: Include disk usage summary.
        include_gpu: Include NVIDIA GPU / CUDA summary.

    Returns:
        JSON string with system diagnostics.
    """
    params: Dict[str, Any] = {}

    # Hermes dispatch passes the model-provided tool arguments as the first
    # positional argument, usually a dict. Preserve direct Python calls too.
    if args and isinstance(args[0], dict):
        params.update(args[0])

    # Ignore Hermes runtime metadata such as task_id/user_task unless we later
    # decide to use it. Only the include_* parameters affect behavior.
    params.update({
        key: value
        for key, value in kwargs.items()
        if key in {"include_network", "include_disks", "include_gpu"}
    })

    include_network = bool(params.get("include_network", True))
    include_disks = bool(params.get("include_disks", True))
    include_gpu = bool(params.get("include_gpu", True))

    report: Dict[str, Any] = {
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "kernel": platform.release(),
        "os_release": _read_os_release(),
        "paths": _basic_paths(),
        "commands": {},
        "safety": {
            "read_only": True,
            "uses_sudo": False,
            "writes_files": False,
            "destructive_actions": False,
        },
    }

    report["commands"]["uname"] = _run_command(["uname", "-a"])
    report["commands"]["memory"] = _run_command(["free", "-h"])

    if include_disks:
        report["commands"]["disk_usage"] = _run_command(["df", "-h"])
        report["commands"]["block_devices"] = _run_command(["lsblk", "-o", "NAME,SIZE,FSTYPE,MOUNTPOINTS,MODEL"])

    if include_network:
        report["commands"]["network_interfaces"] = _run_command(["ip", "-br", "addr"])

    if include_gpu:
        report["commands"]["nvidia_smi"] = _run_command(["nvidia-smi"])
        report["commands"]["nvidia_smi_query"] = _run_command([
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total,memory.used,temperature.gpu",
            "--format=csv,noheader",
        ])
        report["commands"]["cuda_nvcc"] = _run_command(["nvcc", "--version"])

    return json.dumps(report, ensure_ascii=False, indent=2)


def check_vidnux_requirements() -> bool:
    """Vidnux diagnostics have no external requirements beyond local shell commands."""
    return True


VIDNUX_STATUS_SCHEMA = {
    "name": "vidnux_status",
    "description": (
        "Read-only diagnostic tool for the Vidnux Ubuntu workstation. "
        "Reports OS, kernel, Python, NVIDIA GPU/CUDA status, RAM, disks, "
        "network interfaces, and key command paths. Does not use sudo or modify files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "include_network": {
                "type": "boolean",
                "description": "Whether to include network interface information.",
                "default": True,
            },
            "include_disks": {
                "type": "boolean",
                "description": "Whether to include disk usage and block device information.",
                "default": True,
            },
            "include_gpu": {
                "type": "boolean",
                "description": "Whether to include NVIDIA GPU and CUDA information.",
                "default": True,
            },
        },
        "required": [],
    },
}


registry.register(
    name="vidnux_status",
    toolset="vidnux",
    schema=VIDNUX_STATUS_SCHEMA,
    handler=vidnux_status,
    check_fn=check_vidnux_requirements,
    description="Vidnux workstation diagnostics",
    emoji="🖥️",
    max_result_size_chars=30000,
)
