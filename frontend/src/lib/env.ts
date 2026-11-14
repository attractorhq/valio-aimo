/**
 * Synthetic UI mocks are a development and test aid only. A production build never shows them: if the gateway
 * is slow or unreachable, the UI retries and shows the real error instead of fake data.
 */
export const ALLOW_MOCK = !import.meta.env.PROD
