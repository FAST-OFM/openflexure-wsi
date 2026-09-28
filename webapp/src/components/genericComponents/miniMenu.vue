<template>
  <div ref="mini-menu" class="mini-menu">
    <a v-if="!withButton" class="icon" @click.prevent="isOpen = !isOpen">
      <span class="material-symbols-outlined"> {{ icon }} </span>
    </a>
    <button
      v-if="withButton"
      class="ofm-top-nav-square-button material-icon uk-button uk-button-default"
      type="button"
      @click="isOpen = !isOpen"
    >
      <span class="material-symbols-outlined"> {{ icon }} </span>
    </button>
    <div
      v-if="isOpen"
      class="dropdown-menu ofm-close-pars ofm-opaque-element"
      :class="{ 'with-button': withButton }"
    >
      <slot :close="close"></slot>
    </div>
  </div>
</template>

<script>
export default {
  name: "MiniMenu",

  props: {
    icon: {
      type: String,
      default: "more_vert",
      required: false,
    },
    withButton: {
      type: Boolean,
      default: false,
      required: false,
    },
  },

  data() {
    return {
      isOpen: false,
    };
  },

  mounted() {
    window.addEventListener("click", this.handleClickOutside);
  },

  beforeUnmount() {
    window.removeEventListener("click", this.handleClickOutside);
  },

  methods: {
    handleClickOutside(event) {
      if (!this.$refs["mini-menu"].contains(event.target)) {
        this.isOpen = false;
      }
    },
    close() {
      this.isOpen = false;
    },
  },
};
</script>

<style lang="less" scoped>
@import url("../../assets/less/variables.less");

.mini-menu {
  position: relative;
}

.dropdown-menu {
  position: absolute;
  top: 25px;
  right: 0;
  border: 1px solid #ccc;
  border-radius: 4px;
  max-height: 250px;
  width: 180px;
  overflow-y: auto;
  z-index: 1000;
  padding-top: 5px;
}

.dropdown-menu.with-button {
  top: 40px;
}

.icon {
  user-select: none;
  color: #888;
}

.icon:hover {
  color: #c5247f;
}
</style>
