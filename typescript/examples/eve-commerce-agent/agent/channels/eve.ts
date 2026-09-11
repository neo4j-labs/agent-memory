/**
 * The HTTP channel, plus where the shopper's identity comes from.
 *
 * Route auth is the trust boundary: whatever an `AuthFn` returns becomes
 * `ctx.session.auth.current` for the turn, and that is what the `shopper` memory
 * slot scopes on. This demo reads the shopper id from an `x-shopper-id` request
 * header so the README's two curl sessions can be two different people.
 *
 * ⚠️ A trusted request header is NOT authentication. `demoShopperHeader()`
 * therefore refuses to run unless the process is a local dev server
 * (`eve dev` sets `EVE_DEV=1`) or you have explicitly opted in with
 * `ALLOW_DEMO_SHOPPER_HEADER=1`. In production the walk falls through to
 * `vercelOidc()`, `localDev()` and finally `placeholderAuth()`, which rejects
 * browser traffic until you put your own verifier (Auth.js, Clerk, your own
 * JWT/OIDC check) in its place. Swap `demoShopperHeader()` for that verifier and
 * nothing else in this example changes: the memory slot keeps scoping on
 * `principalId`.
 */

import { localDev, placeholderAuth, vercelOidc, type AuthFn } from "eve/channels/auth";
import { eveChannel } from "eve/channels/eve";

const DEFAULT_SHOPPER_ID = "demo-shopper";

function isLocalDevServer(): boolean {
  if (process.env.EVE_DEV === "1") return true;
  return process.env.VERCEL === "1" && process.env.VERCEL_ENV === "development";
}

function demoShopperHeader(): AuthFn<Request> {
  return (request) => {
    if (!isLocalDevServer() && process.env.ALLOW_DEMO_SHOPPER_HEADER !== "1") return null;
    const header = request.headers.get("x-shopper-id")?.trim();
    const shopperId =
      header !== undefined && header !== ""
        ? header
        : (process.env.DEMO_SHOPPER_ID ?? DEFAULT_SHOPPER_ID);
    return {
      attributes: { source: header === undefined || header === "" ? "default" : "header" },
      authenticator: "demo-shopper-header",
      principalId: shopperId,
      principalType: "user",
    };
  };
}

export default eveChannel({
  auth: [demoShopperHeader(), vercelOidc(), localDev(), placeholderAuth()],
});
