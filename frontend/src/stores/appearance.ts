import { defineStore } from "pinia";
import { api } from "../api";
import type {
  AdminAppearance,
  AppearanceContext,
  UserAppearance,
} from "../types";

const fallback: AppearanceContext = {
  effective_season: "spring",
  art_level: "standard",
  reduce_motion: false,
  theme_mode: "auto",
};

function applyRootAttributes(context: AppearanceContext) {
  const root = document.documentElement;
  root.dataset.season = context.effective_season;
  root.dataset.artLevel = context.art_level;
  root.dataset.reduceMotion = context.reduce_motion ? "true" : "false";
}

export const useAppearanceStore = defineStore("appearance", {
  state: () => ({
    context: { ...fallback } as AppearanceContext,
    user: null as UserAppearance | null,
    admin: null as AdminAppearance | null,
    loaded: false,
  }),
  actions: {
    async loadContext() {
      try {
        this.context = await api.get<AppearanceContext>("/appearance/context");
      } catch {
        this.context = { ...fallback };
      }
      applyRootAttributes(this.context);
      this.loaded = true;
      return this.context;
    },
    async loadUser() {
      this.user = await api.get<UserAppearance>("/me/appearance");
      return this.user;
    },
    async saveUser(payload: Omit<UserAppearance, "user_id" | "version" | "updated_at">) {
      if (!this.user) await this.loadUser();
      this.user = await api.patch<UserAppearance>(
        "/me/appearance",
        payload,
        { "If-Match": String(this.user?.version || 1) },
      );
      await this.loadContext();
      return this.user;
    },
    async loadAdmin() {
      this.admin = await api.get<AdminAppearance>("/admin/appearance");
      return this.admin;
    },
    async saveAdmin(payload: Omit<AdminAppearance, "version">) {
      if (!this.admin) await this.loadAdmin();
      this.admin = await api.patch<AdminAppearance>(
        "/admin/appearance",
        payload,
        { "If-Match": String(this.admin?.version || 1) },
      );
      await this.loadContext();
      return this.admin;
    },
  },
});
