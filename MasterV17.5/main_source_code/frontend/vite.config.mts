import config from "./plugins/vite.config.mts";

export default (environment: Parameters<typeof config>[0]) => config(environment);
