"""A Python device for the browser end-to-end tests (python.e2e.cjs): plays
Studio's part over the same WebRTC, so a browser page and Python pair and form
one mesh. Speaks JSON lines: events on stdout, commands on stdin.

    py_device.py owner   serve signaling; write an invitation as the mesh owner
                         (Studio's operator key) and wait; stdin "admit" / "differ"
                         decides; then go online in the mesh as this machine (its
                         node key, admitted by the owner)
    py_device.py join    stdin: {"link": ...} from a browser inviter; join, show the
                         code, wait for the admission; then go online in the mesh
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys

from secdogie_identity import Identity
from secdogie_transport.mesh_rtc import MeshNode
from secdogie_transport.pairing import (
    create_admission,
    create_invite,
    decode_invite,
    encode_invite,
    verify_invite,
)
from secdogie_transport.pairing_session import invite_device, join_with_invite
from secdogie_transport.signal_server import serve_signaling


def say(**event) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


async def line() -> str:
    return (await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)).strip()


def mesh_events(node: MeshNode):
    last = {}

    def changed():
        snap = node.snapshot()
        view = {d["did"]: d["state"] for d in snap["devices"]}
        if view != last.get("v"):
            last["v"] = view
            say(event="mesh", devices=snap["devices"], online=snap["online"])

    return changed


async def owner(page_base: str) -> None:
    srv = await serve_signaling()
    operator, node = Identity.generate(), Identity.generate()  # Studio: the operator owns, the node is the device
    node_adm = create_admission(operator, "home", node.did, invite_id=secrets.token_hex(16), name="书房的电脑")
    invite = create_invite(operator, "home", room=f"pair-{secrets.token_hex(16)}", signal=srv.url)
    say(event="invite", link=f"{page_base}#invite={encode_invite(invite)}", signal=srv.url,
        owner=operator.did, node=node.did)

    async def decide():
        return (await line()) == "admit"

    try:
        admission = await invite_device(operator, invite, on_code=lambda c: say(event="code", code=c.code,
                                                                                 did=c.did, name=c.name),
                                        decide=decide, roster={"mesh": "home", "admissions": [node_adm]})
    except Exception as e:  # noqa: BLE001 - the test reads the outcome
        say(event="ended", reason=f"{type(e).__name__}: {e}")
        await line()
        return
    say(event="admitted", admission=admission)
    mesh = MeshNode(node, mesh="home", owners=[operator.did], admission=node_adm, admissions=[admission],
                    name="书房的电脑", signal_url=srv.url, on_note=lambda n: say(event="note", **n))
    mesh.on_change = mesh_events(mesh)
    await mesh.start()
    while (cmd := await line()) and cmd != "quit":
        if cmd.startswith("say "):
            say(event="said", reached=await mesh.say(cmd[4:]))
    await mesh.stop()
    await srv.close()


async def join() -> None:
    device = Identity.generate()
    link = json.loads(await line())["link"]
    checked = verify_invite(decode_invite(link.split("#invite=", 1)[1]))
    if not checked.ok:
        say(event="ended", reason=checked.reason)
        return
    invite = checked.value
    admission, roster = await join_with_invite(device, invite, my_name="书房的电脑",
                                               on_code=lambda c: say(event="code", code=c.code, did=c.did))
    say(event="admitted", admission=admission, roster=roster)
    mesh = MeshNode(device, mesh=invite["mesh"], owners=[invite["inviter"]], admission=admission,
                    admissions=(roster or {}).get("admissions", []), name="书房的电脑", signal_url=invite["signal"],
                    on_note=lambda n: say(event="note", **n))
    mesh.on_change = mesh_events(mesh)
    await mesh.start()
    while (cmd := await line()) and cmd != "quit":
        if cmd.startswith("say "):
            say(event="said", reached=await mesh.say(cmd[4:]))
    await mesh.stop()


if __name__ == "__main__":
    mode = sys.argv[1]
    asyncio.run(owner(sys.argv[2]) if mode == "owner" else join())
