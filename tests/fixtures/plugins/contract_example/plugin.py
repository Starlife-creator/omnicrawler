"""Owned offline contract fixture; no network or installed plugin required."""

PLUGIN_METADATA = {
    "name": "contract_example",
    "version": "1.0.0",
    "execution_mode": "subprocess",
    "permissions": [],
    "license": "MIT",
}


def handle(operation, payload):
    return {"operation": operation, "payload": payload}
