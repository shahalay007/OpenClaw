const OPENCLAW_SEND_MODULE_PREFIX = "file:///usr/local/lib/node_modules/openclaw/dist/send-";
const PATCH_REPLACEMENTS = [
  {
    search: 'const outboundLog = createSubsystemLogger("gateway/channels/whatsapp").child("outbound");\n',
    replacement: `const outboundLog = createSubsystemLogger("gateway/channels/whatsapp").child("outbound");
function isWhatsAppOutboundDisabled() {
\treturn process.env.WHATSAPP_DISABLE_OUTBOUND === "1";
}
function resolveAllowlistedWhatsAppGroupJids(cfg) {
\treturn Object.entries(cfg?.channels?.whatsapp?.groups ?? {}).filter(([, entry]) => entry !== false && entry?.enabled !== false && entry?.allow !== false).map(([groupId]) => toWhatsappJid(groupId));
}
function assertAllowedWhatsAppGroupTarget(jid, cfg) {
\tif (isWhatsAppOutboundDisabled()) throw new Error("WhatsApp outbound disabled by policy");
\tif (!jid.endsWith("@g.us")) return;
\tconst allowlisted = resolveAllowlistedWhatsAppGroupJids(cfg);
\tif (cfg?.channels?.whatsapp?.groupPolicy !== "allowlist" || allowlisted.length === 0 || !allowlisted.includes(jid)) throw new Error(\`WhatsApp outbound blocked: group target \${jid} is not allowlisted\`);
}
`
  },
  {
    search: "\tconst cfg = options.cfg ?? loadConfig();\n\tconst account = resolveWhatsAppAccount({",
    replacement: "\tconst cfg = options.cfg ?? loadConfig();\n\tassertAllowedWhatsAppGroupTarget(jid, cfg);\n\tconst account = resolveWhatsAppAccount({"
  },
  {
    search: '\ttry {\n\t\tconst redactedJid = redactIdentifier(toWhatsappJid(chatJid));',
    replacement: '\ttry {\n\t\tconst cfg = options.cfg ?? loadConfig();\n\t\tconst jid = toWhatsappJid(chatJid);\n\t\tassertAllowedWhatsAppGroupTarget(jid, cfg);\n\t\tconst redactedJid = redactIdentifier(jid);'
  },
  {
    search: "\ttry {\n\t\tconst jid = toWhatsappJid(to);\n\t\tconst redactedJid = redactIdentifier(jid);",
    replacement: "\ttry {\n\t\tconst cfg = options.cfg ?? loadConfig();\n\t\tconst jid = toWhatsappJid(to);\n\t\tassertAllowedWhatsAppGroupTarget(jid, cfg);\n\t\tconst redactedJid = redactIdentifier(jid);"
  }
];
const SOURCE_DECODER = new TextDecoder();

export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context);

  if (!url.startsWith(OPENCLAW_SEND_MODULE_PREFIX) || result.format !== "module" || result.source == null) {
    return result;
  }

  const source = asString(result.source);

  if (!source.includes("async function sendMessageWhatsApp(") || source.includes("assertAllowedWhatsAppGroupTarget")) {
    return result;
  }

  return {
    ...result,
    source: patchWhatsappSendModule(source),
    shortCircuit: true
  };
}

function patchWhatsappSendModule(source) {
  return PATCH_REPLACEMENTS.reduce(
    (patchedSource, replacement) => replaceOnce(patchedSource, replacement.search, replacement.replacement),
    source
  );
}

function replaceOnce(source, search, replacement) {
  if (!source.includes(search)) {
    throw new Error(`Patch anchor not found: ${search}`);
  }

  return source.replace(search, replacement);
}

function asString(source) {
  if (typeof source === "string") {
    return source;
  }

  if (source instanceof Uint8Array) {
    return SOURCE_DECODER.decode(source);
  }

  return String(source);
}
