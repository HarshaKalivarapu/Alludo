import { handleApi, type Env } from "../../src/api";

// Every /api/* request lands here; everything else is a static file from public/.
export const onRequest: PagesFunction<Env> = ({ request, env }) => handleApi(new URL(request.url), env);
