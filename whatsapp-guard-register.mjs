import { register } from "node:module";
import { pathToFileURL } from "node:url";
import { WebSocket } from "ws";

const SEND_GUARD_MARKER = Symbol.for("openclaw.wsSendGuardInstalled");

if (process.argv[1] === "/hostinger/server.mjs") {
  installWebSocketSendGuard();
}

register(pathToFileURL("/hostinger/whatsapp-guard-loader.mjs").href, import.meta.url);

function installWebSocketSendGuard() {
  if (WebSocket.prototype[SEND_GUARD_MARKER]) {
    return;
  }

  const originalSend = WebSocket.prototype.send;

  WebSocket.prototype.send = function guardedSend(data, options, callback) {
    if (this.readyState === WebSocket.CONNECTING) {
      this.once("open", () => {
        originalSend.call(this, data, options, callback);
      });
      return;
    }

    return originalSend.call(this, data, options, callback);
  };

  WebSocket.prototype[SEND_GUARD_MARKER] = true;
}
