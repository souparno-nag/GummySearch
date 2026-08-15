// SSR is off project-wide (R10): this frontend is a pure client of the FastAPI REST
// API, and adapter-static's SPA fallback mode requires SSR disabled to serve every
// route from the same client-rendered shell.
export const ssr = false;
