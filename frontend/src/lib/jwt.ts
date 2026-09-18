interface AccessTokenPayload {
  sub: string;
  role: string;
}

/**
 * Decode a JWT access token's payload claims client-side, without verifying the signature.
 * This is safe here because the decoded value is only ever used for local UI purposes (scoping
 * localStorage keys by user ID) -- every real authorization decision still happens server-side
 * against the same token, so a tampered token would simply fail there, not grant anything here.
 *
 * Returns `null` for a malformed token rather than throwing, since this is called eagerly on
 * every render and a corrupt/unexpected token shape must degrade gracefully, not crash the app.
 */
export function decodeAccessTokenPayload(token: string): AccessTokenPayload | null {
  try {
    const [, payloadSegment] = token.split(".");
    if (!payloadSegment) return null;
    const unpadded = payloadSegment.replace(/-/g, "+").replace(/_/g, "/");
    // JWTs omit base64 padding; atob requires a length that's a multiple of 4.
    const base64 = unpadded.padEnd(unpadded.length + ((4 - (unpadded.length % 4)) % 4), "=");
    const json = atob(base64);
    const payload = JSON.parse(json) as Partial<AccessTokenPayload>;
    if (typeof payload.sub !== "string" || typeof payload.role !== "string") return null;
    return { sub: payload.sub, role: payload.role };
  } catch {
    return null;
  }
}
