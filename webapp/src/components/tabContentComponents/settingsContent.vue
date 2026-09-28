<template>
  <div class="settings-shell uk-height-1-1">
    <nav class="settings-nav" aria-label="Settings sections">
      <ul class="uk-nav uk-nav-default">
        <template v-for="group in visibleGroups" :key="group.id">
          <li class="uk-nav-header">{{ group.title }}</li>
          <li v-for="item in group.tabs" :key="`setting-${item.id}-tab-icon`">
            <tab-icon
              :id="`setting-${item.id}-tab-icon`"
              :tab-i-d="item.id"
              :show-title="false"
              :show-tooltip="false"
              :require-connection="item.requireConnection"
              :current-tab="currentTab"
              @set-tab="setTab"
            >
              {{ item.title }}
            </tab-icon>
          </li>
        </template>
        <li class="uk-nav-header">Setup</li>
        <li>
          <button
            type="button"
            class="uk-button uk-button-default uk-button-small uk-width-1-1"
            @click="$emit('startCalModal')"
          >
            Calibration wizard
          </button>
        </li>
      </ul>
    </nav>
    <main class="view-component uk-padding-small">
      <tab-content
        v-for="item in allTabs"
        :id="`setting-${item.id}-tab-content`"
        :key="`setting-${item.id}-tab-content`"
        :tab-i-d="item.id"
        :require-connection="item.requireConnection"
        :current-tab="currentTab"
      >
        <component :is="item.component" />
      </tab-content>
    </main>
  </div>
</template>

<script>
import { markRaw } from "vue";
import { useSettingsStore } from "@/stores/settings.js";
import TabIcon from "../genericComponents/tabIcon.vue";
import TabContent from "../genericComponents/tabContent.vue";
import DisplaySettings from "./settingsComponents/displaySettings.vue";
import StageControlSettings from "./settingsComponents/stageControlSettings.vue";
import IlluminationSettings from "./settingsComponents/illuminationSettings.vue";
import CameraSettings from "./settingsComponents/cameraSettings.vue";
import CameraPreviewSettings from "./settingsComponents/cameraPreviewSettings.vue";
import CameraWhiteCalibrationSettings from "./settingsComponents/cameraWhiteCalibrationSettings.vue";
import RgFlatFieldPage from "./settingsComponents/rgFlatFieldPage.vue";
import AutofocusSettings from "./settingsComponents/autofocusSettings.vue";
import StageSettings from "./settingsComponents/stageSettings.vue";
import ZBacklashPage from "./settingsComponents/zBacklashPage.vue";
import CSMSettings from "./settingsComponents/CSMSettings.vue";

export default {
  name: "SettingsContent",
  components: { TabIcon, TabContent },
  emits: ["startCalModal"],
  data() {
    return {
      currentTab: "display",
      groups: [
        {
          id: "application",
          title: "Application",
          tabs: [
            {
              id: "display",
              title: "Appearance",
              requireConnection: false,
              component: markRaw(DisplaySettings),
              requiredThings: [],
            },
          ],
        },
        {
          id: "control",
          title: "Control",
          tabs: [
            {
              id: "stage-control",
              title: "Manual movement",
              requireConnection: false,
              component: markRaw(StageControlSettings),
              requiredThings: ["stage"],
            },
            {
              id: "illumination",
              title: "Illumination",
              requireConnection: true,
              component: markRaw(IlluminationSettings),
              requiredThings: ["illumination"],
            },
          ],
        },
        {
          id: "camera",
          title: "Camera",
          tabs: [
            {
              id: "camera-image",
              title: "Image",
              requireConnection: true,
              component: markRaw(CameraSettings),
              requiredThings: ["camera"],
            },
            {
              id: "camera-preview",
              title: "Preview",
              requireConnection: true,
              component: markRaw(CameraPreviewSettings),
              requiredThings: ["camera"],
            },
            {
              id: "camera-white",
              title: "WHITE calibration",
              requireConnection: true,
              component: markRaw(CameraWhiteCalibrationSettings),
              requiredThings: ["camera"],
            },
            {
              id: "camera-rg-flatfield",
              title: "R/G flat-field",
              requireConnection: true,
              component: markRaw(RgFlatFieldPage),
              requiredThings: ["rg_flat_field"],
            },
            {
              id: "camera-autofocus",
              title: "Autofocus",
              requireConnection: true,
              component: markRaw(AutofocusSettings),
              requiredThings: ["autofocus"],
            },
          ],
        },
        {
          id: "stage",
          title: "Stage",
          tabs: [
            {
              id: "stage-limits",
              title: "Limits",
              requireConnection: true,
              component: markRaw(StageSettings),
              requiredThings: ["stage"],
            },
            {
              id: "stage-z-backlash",
              title: "Z backlash",
              requireConnection: true,
              component: markRaw(ZBacklashPage),
              requiredThings: ["z_backlash"],
            },
            {
              id: "mapping",
              title: "Camera mapping",
              requireConnection: true,
              component: markRaw(CSMSettings),
              requiredThings: ["camera_stage_mapping"],
            },
          ],
        },
      ],
    };
  },
  computed: {
    visibleGroups() {
      return this.groups
        .map((group) => ({
          ...group,
          tabs: group.tabs.filter(
            (tab) =>
              !tab.requiredThings?.length ||
              tab.requiredThings.every((thing) => this.thingAvailable(thing)),
          ),
        }))
        .filter((group) => group.tabs.length);
    },
    allTabs() {
      return this.visibleGroups.flatMap((group) => group.tabs);
    },
  },
  mounted() {
    const store = useSettingsStore();
    const requested = this.pageFromHash() || store.settingsPage;
    this.activate(this.allTabs.some((tab) => tab.id === requested) ? requested : "display", false);
    window.addEventListener("hashchange", this.restoreFromHash);
  },
  beforeUnmount() {
    window.removeEventListener("hashchange", this.restoreFromHash);
  },
  methods: {
    pageFromHash() {
      const match = window.location.hash.match(/^#settings\/([^/]+)$/);
      return match?.[1] || "";
    },
    restoreFromHash() {
      const requested = this.pageFromHash();
      if (requested && this.allTabs.some((tab) => tab.id === requested)) {
        this.activate(requested, false);
      }
    },
    setTab(event, tab) {
      event?.preventDefault();
      this.activate(tab, true);
    },
    activate(tab, updateHash) {
      this.currentTab = tab;
      useSettingsStore().settingsPage = tab;
      if (updateHash) window.history.replaceState(null, "", `#settings/${tab}`);
    },
  },
};
</script>

<style lang="less" scoped>
@import url("../../assets/less/variables.less");

.settings-shell {
  display: grid;
  grid-template-columns: 230px minmax(0, 1fr);
  min-width: 0;
}

.settings-nav {
  overflow: hidden auto;
  padding: 10px;
  background-color: rgba(180, 180, 180, 0.03);
  border-right: 1px solid rgba(180, 180, 180, 0.25);
}

.settings-nav li > a {
  padding-left: 6px !important;
  border-radius: @button-border-radius;
}

.settings-nav .uk-nav-header:not(:first-child) {
  margin-top: 14px;
}

.view-component {
  min-width: 0;
  overflow: auto;
}

@media (width <= 720px) {
  .settings-shell {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(9rem, 38vh) minmax(0, 1fr);
  }

  .settings-nav {
    border-right: 0;
    border-bottom: 1px solid rgba(180, 180, 180, 0.25);
  }
}
</style>
