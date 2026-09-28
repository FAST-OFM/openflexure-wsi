<template>
  <div class="progress uk-margin-top uk-margin-horizontal-remove uk-padding-remove">
    <div class="indeterminate"></div>
  </div>
</template>

<script>
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings";

export default {
  name: "ProgressBar",

  props: {},

  emits: ["set-tab"],

  computed: {
    ...mapState(useSettingsStore, ["ready"]),
    tooltipOptions: function () {
      var title = this.id.charAt(0).toUpperCase() + this.id.slice(1);
      return `pos: right; title: ${title}; delay: 500`;
    },

    classObject: function () {
      return {
        "tabicon-active": this.currentTab == this.id,
        "uk-disabled": this.requireConnection && !this.ready,
      };
    },
  },

  methods: {
    setThisTab(event) {
      this.$emit("set-tab", event, this.id);
    },
  },
};
</script>

<style lang="less" scoped>
@import url("../../assets/less/variables.less");

.progress {
  position: relative;
  height: 5px;
  display: block;
  width: 100%;
  background-color: rgba(180, 180, 180, 0.15);
  border-radius: 2px;
  background-clip: padding-box;
  margin: 0.5rem 0 1rem;
  overflow: hidden;
}

.progress .determinate {
  position: absolute;
  background-color: inherit;
  top: 0;
  bottom: 0;
  transition: width 0.3s linear;
}

.progress .indeterminate,
.progress .determinate {
  background-color: @global-primary-background;
}

.progress .indeterminate::before {
  content: "";
  position: absolute;
  background-color: inherit;
  top: 0;
  left: 0;
  bottom: 0;
  will-change: left, right;
  animation: indeterminate 2.1s cubic-bezier(0.65, 0.815, 0.735, 0.395) infinite;
}

.progress .indeterminate::after {
  content: "";
  position: absolute;
  background-color: inherit;
  top: 0;
  left: 0;
  bottom: 0;
  will-change: left, right;
  animation: indeterminate-short 2.1s cubic-bezier(0.165, 0.84, 0.44, 1) infinite;
  animation-delay: 1.15s;
}

@keyframes indeterminate {
  0% {
    left: -35%;
    right: 100%;
  }

  60% {
    left: 100%;
    right: -90%;
  }

  100% {
    left: 100%;
    right: -90%;
  }
}

@keyframes indeterminate-short {
  0% {
    left: -200%;
    right: 100%;
  }

  60% {
    left: 107%;
    right: -8%;
  }

  100% {
    left: 107%;
    right: -8%;
  }
}
</style>
