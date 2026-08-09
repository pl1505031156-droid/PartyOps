import { defineStore } from "pinia";
import { api } from "../api";
import type { RuntimeContext, User } from "../types";

interface BootstrapStatus {
  configured: boolean;
  mode: string;
  app_name: string;
  host: string;
  port: number;
  service_url: string;
  lan_candidates: string[];
}

export const useSessionStore = defineStore("session", {
  state: () => ({
    user: null as User | null,
    bootstrap: null as BootstrapStatus | null,
    runtimeContext: null as RuntimeContext | null,
    ready: false,
  }),
  actions: {
    async loadBootstrap() {
      this.bootstrap = await api.get<BootstrapStatus>("/bootstrap/status");
      return this.bootstrap;
    },
    async ensure() {
      if (this.user) {
        if (!this.runtimeContext) await this.loadRuntimeContext();
        return this.user;
      }
      if (this.ready) return null;
      try {
        this.user = await api.get<User>("/auth/me");
        await this.loadRuntimeContext();
      } catch {
        this.user = null;
      } finally {
        this.ready = true;
      }
      return this.user;
    },
    async login(username: string, password: string) {
      this.user = await api.post<User>("/auth/login", { username, password });
      this.ready = true;
      await this.loadRuntimeContext();
      return this.user;
    },
    async loadRuntimeContext() {
      this.runtimeContext = await api.get<RuntimeContext>("/runtime/context");
      return this.runtimeContext;
    },
    async setup(username: string, displayName: string, password: string) {
      await api.post<User>("/bootstrap/host", {
        username,
        display_name: displayName,
        password,
      });
      await this.loadBootstrap();
      return this.login(username, password);
    },
    async logout() {
      await api.post<void>("/auth/logout");
      this.user = null;
      this.runtimeContext = null;
      this.ready = true;
    },
  },
});
