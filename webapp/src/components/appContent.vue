<template>
  <div id="app-content" class="uk-margin-remove uk-padding-remove uk-height-1-1" uk-grid>
    <!-- Initialisation modals -->
    <calibrationWizard ref="calibrationWizard" @on-close="enterApp()"></calibrationWizard>
    <!-- Vertical tab bar -->
    <div id="switcher-left-container">
      <div
        id="switcher-left"
        class="uk-flex uk-flex-column uk-padding-remove uk-width-auto uk-height-1-1 uk-text-center"
      >
        <!-- For each top tab -->
        <template v-for="(item, index) in topTabs" :key="item.id + '-tab-icon'">
          <!-- Render the tab icon -->
          <tabIcon
            :id="item.id + '-tab-icon'"
            :tab-i-d="item.id"
            :title="item.title"
            :require-connection="true"
            :current-tab="currentTab"
            :class="item.class"
            @set-tab="setTab"
          >
            <img
              v-if="item.iconURL"
              style="filter: grayscale(100%); width: 22px; margin-top: 5px; margin-bottom: 8px"
              :src="item.iconURL"
            />
            <span v-if="!item.iconURL" class="material-symbols-outlined">
              {{ item.icon }}
            </span>
          </tabIcon>
          <!-- Add a divider if item.divide is true -->
          <hr v-if="item.divide" :key="'tab-divider-' + index" />
        </template>

        <hr id="extension-tab-divider" />

        <!-- For each bottom tab -->
        <template v-for="(item, index) in bottomTabs" :key="item.id + '-tab-icon'">
          <!-- Render the tab icon -->
          <tabIcon
            :id="item.id + '-tab-icon'"
            :tab-i-d="item.id"
            :title="item.title"
            :require-connection="true"
            :current-tab="currentTab"
            :class="item.class"
            @set-tab="setTab"
          >
            <span class="material-symbols-outlined">{{ item.icon }}</span>
          </tabIcon>
          <!-- Add a divider if item.divide is true -->
          <hr v-if="item.divide" :key="'tab-divider-' + index" />
        </template>
      </div>
    </div>

    <!-- Corresponding vertical tab content -->
    <div
      id="container-left"
      ref="containerLeft"
      class="uk-padding-remove uk-height-1-1 uk-width-expand"
    >
      <tabContent
        v-for="item in allTabs"
        :id="item.id + '-tab-content'"
        :key="item.id + '-tab-content'"
        :tab-i-d="item.id"
        :require-connection="true"
        :current-tab="currentTab"
      >
        <component :is="item.component" v-on="item.events" @scroll-top="scrollToTop"></component>
      </tabContent>
    </div>
  </div>
</template>

<script>
// Import generic components
import tabIcon from "./genericComponents/tabIcon.vue";
import tabContent from "./genericComponents/tabContent.vue";

// Import new content components
import aboutContent from "./tabContentComponents/aboutContent.vue";
import controlContent from "./tabContentComponents/controlContent.vue";
import loggingContent from "./tabContentComponents/loggingContent.vue";
import powerContent from "./tabContentComponents/powerContent.vue";
import galleryContent from "./tabContentComponents/galleryContent.vue";
import settingsContent from "./tabContentComponents/settingsContent.vue";
import slideScanContent from "./tabContentComponents/slideScanContent.vue";
import viewContent from "./tabContentComponents/viewContent.vue";
import { markRaw } from "vue";
import { eventBus } from "../eventBus.js";
import { useSettingsStore } from "@/stores/settings.js";

// Import modal components for device initialisation
import calibrationWizard from "./modalComponents/calibrationWizard.vue";

// Export main app
export default {
  name: "AppContent",

  components: {
    tabIcon,
    tabContent,
    calibrationWizard,
  },
  data: function () {
    return {
      currentTab: "view",
      bottomTabs: [
        {
          id: "settings",
          title: "Settings",
          icon: "settings",
          component: markRaw(settingsContent),
          class: "uk-margin-auto-top",
          events: {
            startCalModal: this.forceStartCalModal,
          },
        },
        {
          id: "logging",
          title: "Logging",
          icon: "assignment_late",
          component: markRaw(loggingContent),
          events: {},
        },
        {
          id: "about",
          title: "About",
          icon: "info",
          component: markRaw(aboutContent),
          events: {},
        },
        {
          id: "power",
          title: "Power",
          icon: "power_settings_new",
          component: markRaw(powerContent),
          events: {},
        },
      ],
      coreTopTabs: [
        {
          id: "view",
          title: "View",
          icon: "visibility",
          component: markRaw(viewContent),
          requiredThings: [],
          events: {},
        },
        {
          id: "control",
          title: "Control",
          icon: "gamepad",
          component: markRaw(controlContent),
          requiredThings: [],
          events: {},
        },
        {
          id: "slide-scan",
          title: "Slide Scan",
          icon: "settings_overscan",
          component: markRaw(slideScanContent),
          requiredThings: ["smart_scan"],
          events: {},
        },
        {
          id: "gallery",
          title: "Gallery",
          icon: "photo_library",
          component: markRaw(galleryContent),
          requiredThings: ["smart_scan"],
          events: {},
        },
      ],
    };
  },

  computed: {
    tabOrder: function () {
      var ind = [];
      for (const tab of this.topTabs) {
        ind.push(tab.id);
      }
      for (const tab of this.bottomTabs) {
        ind.push(tab.id);
      }
      return ind;
    },

    topTabs: function () {
      // Filter core top tabs based on available Things. Once Things can specify a
      // custom tab those will need to be added here
      return this.coreTopTabs.filter((tab) => {
        if (!tab.requiredThings || tab.requiredThings.length === 0) return true;
        return tab.requiredThings.every((thing) => this.thingAvailable(thing));
      });
    },
    allTabs() {
      return [...this.topTabs, ...this.bottomTabs];
    },
    currentTabIndex: function () {
      return this.tabOrder.indexOf(this.currentTab);
    },
  },

  mounted() {
    const store = useSettingsStore();
    // A global signal listener to switch tab
    eventBus.on("globalSwitchTab", this.handleGlobalSwitchTab);
    // A global signal listener to increment tab
    eventBus.on("globalIncrementTab", this.handleGlobalIncrementTab);
    // A global signal listener to decrement tab
    eventBus.on("globalDecrementTab", this.handleGlobalDecrementTab);
    window.addEventListener("hashchange", this.restoreTabFromHash);
    this.restoreTabFromHash();
    if (store.ready) {
      this.startCalModalIfNeeded();
    }
  },

  beforeUnmount() {
    // closing signals
    eventBus.off("globalSwitchTab", this.handleGlobalSwitchTab);
    eventBus.off("globalIncrementTab", this.handleGlobalIncrementTab);
    eventBus.off("globalDecrementTab", this.handleGlobalDecrementTab);
    window.removeEventListener("hashchange", this.restoreTabFromHash);
  },

  methods: {
    // These methods are used for opening and closing signals.
    // They are used instead of anonymous arrow functions so we can
    // call close on the same function called with open.
    handleGlobalSwitchTab: function (tabID) {
      this.activateTab(tabID);
    },
    handleGlobalIncrementTab: function () {
      this.incrementTabBy(1);
    },
    handleGlobalDecrementTab: function () {
      this.incrementTabBy(-1);
    },
    setTab: function (event, tab) {
      event?.preventDefault();
      this.activateTab(tab);
    },
    incrementTabBy: function (n) {
      const newIndex =
        (((this.currentTabIndex + n) % this.tabOrder.length) + this.tabOrder.length) %
        this.tabOrder.length;
      const newId = this.tabOrder[newIndex];
      this.activateTab(newId);
    },
    activateTab(tabID) {
      if (!this.tabOrder.includes(tabID)) return;
      this.currentTab = tabID;
      const settingsStore = useSettingsStore();
      const hash = tabID === "settings" ? `#settings/${settingsStore.settingsPage}` : `#${tabID}`;
      window.history.replaceState(null, "", hash);
    },
    restoreTabFromHash() {
      const topLevel = window.location.hash.replace(/^#/, "").split("/")[0];
      if (this.tabOrder.includes(topLevel)) this.currentTab = topLevel;
    },
    forceStartCalModal() {
      this.$refs.calibrationWizard.force_show();
    },
    startCalModalIfNeeded: function () {
      this.$refs.calibrationWizard.show_if_needed();
    },
    enterApp: function () {
      // Stuff to do once connected and all init modals are finished
    },
    scrollToTop() {
      this.$refs.containerLeft.scrollTo({ top: 0 });
    },
  },
};
</script>

<style scoped lang="less">
.window-container {
  width: 100%;
  height: 100%;
}

#component-left {
  width: 100%;
  height: 100%;
}

#container-left {
  overflow: auto;
  background-color: rgba(180, 180, 180, 0.025);
  width: 100%;
  height: 100%;
}

#switcher-left {
  width: 85px;
  padding-top: 2px !important;
}

#switcher-left-container {
  margin: 0;
  padding: 0;
  overflow: hidden auto;
  height: 100%;
  background-color: rgba(180, 180, 180, 0.1);
  border-width: 0 1px 0 0;
  border-style: solid;
  border-color: rgba(180, 180, 180, 0.25);
}

#switcher-left a {
  padding: 10px 8px;
}
</style>
