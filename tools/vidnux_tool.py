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



def nas_mount_check(*args, **kwargs) -> str:
    """
    Read-only NAS mount diagnostic for Vidnux.

    Checks the default VIDNAS public mount, NAS reachability, mount metadata,
    disk space, and relevant network interface state. Does not write files.
    """
    params: Dict[str, Any] = {}

    if args and isinstance(args[0], dict):
        params.update(args[0])

    params.update({
        key: value
        for key, value in kwargs.items()
        if key in {"mount_path", "nas_ip", "nas_interface", "include_ping"}
    })

    mount_path = str(params.get("mount_path", "/mnt/vidnas_public"))
    nas_ip = str(params.get("nas_ip", "192.168.61.186"))
    nas_interface = str(params.get("nas_interface", "enp17s0"))
    include_ping = bool(params.get("include_ping", True))

    report: Dict[str, Any] = {
        "host": platform.node(),
        "mount_path": mount_path,
        "nas_ip": nas_ip,
        "nas_interface": nas_interface,
        "safety": {
            "read_only": True,
            "uses_sudo": False,
            "writes_files": False,
            "destructive_actions": False,
        },
        "path_checks": {
            "exists": os.path.exists(mount_path),
            "is_dir": os.path.isdir(mount_path),
            "is_mount": os.path.ismount(mount_path),
        },
        "commands": {},
    }

    report["commands"]["findmnt_mount"] = _run_command(["findmnt", mount_path])
    report["commands"]["df_mount"] = _run_command(["df", "-h", mount_path])
    report["commands"]["mount_matching_path"] = _run_command(["sh", "-c", f"mount | grep -F -- {mount_path!r} || true"])
    report["commands"]["network_interface"] = _run_command(["ip", "-br", "addr", "show", nas_interface])
    report["commands"]["route_to_nas"] = _run_command(["ip", "route", "get", nas_ip])

    if include_ping:
        report["commands"]["ping_nas"] = _run_command(["ping", "-c", "2", "-W", "1", nas_ip], timeout=5)

    # Directory preview is useful but bounded. It is read-only.
    if os.path.isdir(mount_path):
        report["commands"]["directory_preview"] = _run_command(["find", mount_path, "-maxdepth", "1", "-mindepth", "1", "-printf", "%f\n"], timeout=8)

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



NAS_MOUNT_CHECK_SCHEMA = {
    "name": "nas_mount_check",
    "description": (
        "Read-only NAS mount diagnostic for Vidnux. Checks whether the VIDNAS "
        "mount path exists, whether it is mounted, SMB/mount metadata, available "
        "space, NAS reachability, route selection, and the dedicated NAS network "
        "interface. Does not use sudo or write files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mount_path": {
                "type": "string",
                "description": "NAS mount path to inspect.",
                "default": "/mnt/vidnas_public",
            },
            "nas_ip": {
                "type": "string",
                "description": "NAS IP address to ping/check route for.",
                "default": "192.168.61.186",
            },
            "nas_interface": {
                "type": "string",
                "description": "Expected dedicated NAS network interface.",
                "default": "enp17s0",
            },
            "include_ping": {
                "type": "boolean",
                "description": "Whether to ping the NAS IP.",
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

registry.register(
    name="nas_mount_check",
    toolset="vidnux",
    schema=NAS_MOUNT_CHECK_SCHEMA,
    handler=nas_mount_check,
    check_fn=check_vidnux_requirements,
    description="Vidnux NAS mount diagnostics",
    emoji="🗄️",
    max_result_size_chars=30000,
)
