"""수신기 설정. 우선순위: 환경변수 > config.toml(데이터 폴더) > 기본값.

원격 확장용 자리: bind를 LAN/Tailscale 주소로, auth_token을 넣으면
POST /v1/logs가 `Authorization: Bearer <token>` 헤더를 검사한다. 지금 기본값은 127.0.0.1·인증 없음.
"""
import os
import socket
import tomllib
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIR = PACKAGE_DIR / "web"


def default_home():
    return Path(os.environ.get("USAGE_MONITOR_HOME") or Path.home() / ".claude" / "usage-monitor")


@dataclass
class Config:
    bind: str = "127.0.0.1"
    port: int = 4318
    home: Path = None            # 데이터 폴더. db·spool의 기본 위치
    db_path: Path = None
    spool_dir: Path = None
    default_host: str = ""       # host.name이 없는 레코드(Desktop OTel)에 붙일 이름
    auth_token: str = ""         # 비우면 인증 없음
    retention_days: int = 90
    spool_poll_sec: float = 0.5
    obs_cost_usd: float = 2.0    # 마지막 한도 관측 뒤의 추정 비용이 이 값을 넘으면 간격을 기다리지 않고 관측한다. 0이면 끔
    appmeta_enabled: bool = True # 앱 세션 메타데이터로 '결정 필요'·'열어 봄'을 보강한다. 형식이 바뀌어 문제가 되면 끈다
    appmeta_glob: str = ""       # 앱 메타데이터 파일 패턴. 비우면 appmeta.default_glob()

    def __post_init__(self):
        self.home = Path(self.home) if self.home else default_home()
        self.db_path = Path(self.db_path) if self.db_path else self.home / "usage.db"
        self.spool_dir = Path(self.spool_dir) if self.spool_dir else self.home / "spool"
        self.default_host = self.default_host or os.environ.get("COMPUTERNAME") or socket.gethostname()


ENV_KEYS = {
    "bind": "USAGE_MONITOR_BIND",
    "port": "USAGE_MONITOR_PORT",
    "db_path": "USAGE_MONITOR_DB",
    "spool_dir": "USAGE_MONITOR_SPOOL",
    "default_host": "USAGE_MONITOR_DEFAULT_HOST",
    "auth_token": "USAGE_MONITOR_AUTH_TOKEN",
    "retention_days": "USAGE_MONITOR_RETENTION_DAYS",
    "obs_cost_usd": "USAGE_MONITOR_OBS_COST_USD",
}


def load(path=None):
    path = Path(path or os.environ.get("USAGE_MONITOR_CONFIG") or default_home() / "config.toml")
    values = {}
    if path.exists():
        with open(path, "rb") as f:
            values.update(tomllib.load(f))
    for key, env in ENV_KEYS.items():
        if os.environ.get(env):
            values[key] = os.environ[env]
    for key in ("port", "retention_days"):
        if key in values:
            values[key] = int(values[key])
    if isinstance(values.get("appmeta_enabled"), str):     # "false"·"0"·"no"도 끔으로 읽는다
        values["appmeta_enabled"] = values["appmeta_enabled"].strip().lower() not in ("false", "0", "no", "off", "")
    if "obs_cost_usd" in values:
        values["obs_cost_usd"] = float(values["obs_cost_usd"])
    return Config(**{k: v for k, v in values.items() if k in Config.__dataclass_fields__})
