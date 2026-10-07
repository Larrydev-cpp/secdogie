/**
 * The shape of `webrtc/client/web_peer.js` (shipped beside the page as
 * dist/vendor/web_peer.js), so the attach logic is typed and testable with a
 * fake.
 */

export type PeerStateName = 'idle' | 'signaling' | 'waiting' | 'negotiating' | 'connected' | 'disconnected' | 'failed';

export interface PeerInfo {
  readonly code: string | null;
  readonly linkId: number | null;
}

export interface IceServer {
  readonly urls: string | readonly string[];
  readonly username?: string;
  readonly credential?: string;
}

export interface PeerApi {
  startPeerConnection(opts: {
    signalingUrl: string;
    room: string;
    iceServers?: readonly IceServer[];
    log?: (line: string) => void;
  }): Promise<{ peerId: string; room: string }>;
  closePeerConnection(): void;
  sendData(payload: string | ArrayBuffer | Uint8Array): Promise<void>;
  descriptions(): { id: number | null; local: string | null; remote: string | null };
  onMessage(cb: (data: string | ArrayBuffer) => void): () => void;
  onStateChange(cb: (state: PeerStateName, detail: string, info: PeerInfo) => void): () => void;
}
