from dataclasses import dataclass


@dataclass
class DBResult:
    port: int
    password: str
    host: str = "localhost"


@dataclass
class PFResult:
    ports: list[int]
    target: str
    load_balance: str | None = None
