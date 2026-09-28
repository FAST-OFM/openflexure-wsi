import { createApp } from "vue";
import { createPinia } from "pinia";
import piniaPluginPersistedstate from "pinia-plugin-persistedstate";
import App from "./App.vue";
import UIkit from "uikit";

// Import MD icons
import "./assets/fonts/icons.css";

import modalMixin from "@/mixins/modalMixins.js";
import labThingsMixins from "./mixins/labThingsMixins";

// UIKit overrides
UIkit.mixin(
  {
    data: {
      animation: false,
    },
  },
  "accordion",
);

// Create Vue app
const app = createApp(App);
const pinia = createPinia();
pinia.use(piniaPluginPersistedstate);

// Use Pinia
app.use(pinia);

// Use global mixins
app.mixin(modalMixin);
app.mixin(labThingsMixins);

app.mount("#app");
