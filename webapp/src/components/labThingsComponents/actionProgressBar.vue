<template>
  <div class="progress uk-margin-small" :class="inButton ? 'in-button' : 'stand-alone'">
    <div v-if="indeterminateProgressBar" class="indeterminate"></div>
    <div v-else class="determinate" :style="barWidthFromProgress"></div>
  </div>
</template>

<script>
export default {
  name: "ActionProgressBar",

  props: {
    progress: {
      type: Number,
      required: false,
      default: null,
    },
    taskStatus: {
      type: String,
      required: true,
    },
    inButton: {
      type: Boolean,
      required: false,
      default: false,
    },
  },

  computed: {
    barWidthFromProgress: function () {
      var progress = this.progress <= 100 ? this.progress : 100;
      var styleString = progress != null ? `width: ${progress}%` : `width: 0%`;
      return styleString;
    },
    indeterminateProgressBar: function () {
      if (this.taskStatus == "pending") return true;
      if ((this.taskStatus == "running") & !this.progress) {
        return true;
      }
      return false;
    },
  },
};
</script>

<style lang="less" scoped>
@import url("../../assets/less/variables.less");

.in-button {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 4px;
  background-color: transparent;
  border-radius: 0;
  margin: 0;
  overflow: hidden;
  z-index: 1;
}

.stand-alone {
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
