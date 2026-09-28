<template>
  <a
    href="#"
    class="uk-link"
    :class="classObject"
    :uk-tooltip="tooltipOptions ? tooltipOptions : null"
    @click="setThisTab"
  >
    <slot></slot>
    <div v-if="showTitle" class="tabtitle">
      {{ computedTitle }}
    </div>
  </a>
</template>

<script>
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";

export default {
  name: "TabIcon",

  props: {
    tabID: {
      type: String,
      required: true,
    },
    title: {
      type: String,
      required: false,
      default: undefined,
    },
    showTitle: {
      type: Boolean,
      required: false,
      default: true,
    },
    showTooltip: {
      type: Boolean,
      required: false,
      default: true,
    },
    currentTab: {
      type: String,
      required: true,
    },
    clickCallback: {
      type: Function,
      required: false,
      default: null,
    },
    requireConnection: Boolean,
  },

  emits: ["set-tab"],

  computed: {
    ...mapState(useSettingsStore, ["ready"]),
    computedTitle: function () {
      if (this.title !== undefined) {
        return this.title;
      } else {
        // Get the last section of a fully qualified name
        var topName = this.tabID.split(".").pop();
        // Make first character uppercase, then add the rest of the string
        return topName.charAt(0).toUpperCase() + topName.slice(1);
      }
    },

    tooltipOptions: function () {
      if (this.showTooltip) {
        return `pos: right; title: ${this.computedTitle}; delay: 500`;
      } else {
        return false;
      }
    },

    classObject: function () {
      return {
        "tabicon-active": this.currentTab == this.tabID,
        "uk-disabled": this.requireConnection && !this.ready,
      };
    },
  },

  methods: {
    setThisTab(event) {
      event.preventDefault();
      this.$emit("set-tab", event, this.tabID);
      if (this.clickCallback) {
        this.clickCallback();
      }
    },
  },
};
</script>

<style lang="less" scoped>
// Custom UIkit CSS modifications
@import url("../../assets/less/variables.less");

.tabicon-active {
  color: #fff !important;
  background-color: @global-primary-background !important;
}

.tabtitle {
  margin: auto;
  max-width: 70px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 85%;
  text-transform: capitalize;
}

a:hover,
.uk-link:hover {
  text-decoration: none !important;
}
</style>
