"""Studio's App side of the operator dialogue with its own node.

``check_link`` opens the same DID-authenticated dialogue the Dialogue App uses
(``secdogie_dialogue``) over loopback UDP and makes one round trip that
changes nothing: it asks the node to retract a memory that does not exist,
and the node refuses. The refusal proves the whole path: the transport, both
DIDs and their allowlists, the dialogue session and the node's request
handling.
"""
from __future__ import annotations

from secdogie_dialogue.app import AppController
from secdogie_dialogue.session import DialogueSession, SessionRouter
from secdogie_identity import Allowlist, Identity
from secdogie_transport import ChannelMux, DirectUDPTransport, Endpoint, PeerIdentity, Session, UDPChannel

from .nodeproc import Ready
from .profile import Profile

PROBE_MEMORY_ID = "studio-link-probe"  # never an id the node holds


class LinkError(RuntimeError):
    """The node could not be reached, or is not this profile's node."""


def check_link(profile: Profile, ready: Ready, *, timeout: float = 10.0) -> str:
    """One harmless request/reply with the node; returns the node's reply line."""
    if ready.did != profile.node_did():
        raise LinkError(f"the node at {ready.host}:{ready.port} is {ready.did}, not this profile's node")
    identity = Identity.load(profile.app_key)
    trust = Allowlist({ready.did})  # this one node, nothing else
    channel = UDPChannel("127.0.0.1", 0)
    ctl = None
    try:
        transport = DirectUDPTransport(identity, channel, allowlist=trust)
        transport.set_peer_endpoint(ready.did, ready.dial_host, ready.port)
        mux = ChannelMux(transport, Session("studio-app", PeerIdentity(identity.did, ""),
                                            active=Endpoint("local", *channel.address)))
        router = SessionRouter(mux)
        session = router.add(DialogueSession(identity, ready.did, router.sender_for(ready.did), trust=trust))
        ctl = AppController(session)
        session.start(0.05)
        ctl.start()
        pkt = ctl.retract_memory(PROBE_MEMORY_ID)

        def answered(c: AppController):
            r = c.request(pkt.request_id)
            return r if r is not None and (r.reply is not None or r.undelivered) else None

        req = ctl.wait_for(answered, timeout)
        if req is None:
            raise LinkError(f"no reply from the node within {timeout:g} s")
        if req.undelivered:
            raise LinkError("the node never received the request")
        return req.reply
    finally:
        if ctl is not None:
            ctl.close()
        channel.close()
