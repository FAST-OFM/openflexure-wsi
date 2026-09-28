<template>
  <div id="viewer-modal" ref="viewerModal" uk-modal>
    <div v-if="selectedItem" id="viewer-modal-body" class="uk-modal-dialog uk-modal-body">
      <div id="viewer-modal-header">
        <h2 id="viewer-modal-title" class="uk-modal-title">
          {{ selectedItem.name }}
        </h2>

        <!-- Scan information -->
        <div class="scan-info">
          <span v-if="selectedItem.card_type === 'Scan'">
            {{ selectedItem.gallery_info.number_of_images }} images | Duration:
            {{ formatDuration(selectedItem.gallery_info.duration) }}
          </span>
        </div>

        <!-- Fullscreen and close buttons -->
        <div class="viewer-header-buttons">
          <button class="uk-float-right" type="button" @click="goFullscreen">
            <span class="material-symbols-outlined">fullscreen</span>
          </button>

          <button class="uk-modal-close uk-float-right" type="button">
            <span class="material-symbols-outlined">close</span>
          </button>
        </div>
      </div>

      <!-- Viewer -->
      <div v-if="imageSource" id="viewer_container" class="viewer_container">
        <OpenSeadragonViewer
          id="openseadragon"
          ref="openseadragon"
          :src="imageSource"
          :brightness="brightness"
          :contrast="contrast"
          :saturation="saturation"
          @entering-fullscreen="enteringFullscreen = true"
        />
      </div>

      <!-- Controls -->
      <div v-if="imageSource" class="viewer-controls">
        <div class="controlsContainer">
          <label>
            Brightness
            <input v-model.number="brightness" type="range" min="0.2" max="1.8" step="0.01" />
          </label>
          <label>
            Contrast
            <input v-model.number="contrast" type="range" min="0.2" max="1.8" step="0.01" />
          </label>
          <label>
            Saturation
            <input v-model.number="saturation" type="range" min="0" max="2" step="0.01" />
          </label>
        </div>

        <button
          type="button"
          class="uk-button uk-button-default reset-button"
          @click="resetFilters"
        >
          Reset Filters
        </button>
      </div>
    </div>
  </div>
</template>

<script>
import UIkit from "uikit";
import OpenSeadragonViewer from "./openSeadragonViewer.vue";
import { formatDuration } from "@/js_utils/formatter.mjs";

export default {
  name: "GalleryModal",
  components: {
    OpenSeadragonViewer,
  },
  props: {
    selectedItem: {
      type: Object,
      default: null,
    },
  },
  data() {
    return {
      brightness: 1,
      contrast: 1,
      saturation: 1,
      enteringFullscreen: false,
      modalEl: null,
      beforeHideHandler: null,
    };
  },
  computed: {
    imageSource() {
      if (this.selectedItem?.card_type === "Scan" && this.selectedItem?.gallery_info.dzi) {
        return `${this.baseUri}/data/${this.selectedItem.path}/${this.selectedItem.gallery_info.dzi}`;
      } else if (this.selectedItem?.card_type === "Capture") {
        return {
          type: "image",
          url: `${this.baseUri}/data/${this.selectedItem.path}`,
        };
      }
      return null;
    },
  },
  mounted() {
    this.modalEl = this.$refs.viewerModal;
    this.beforeHideHandler = (event) => {
      if (this.enteringFullscreen) {
        event.preventDefault();
        this.enteringFullscreen = false;
      }
    };
    this.modalEl.addEventListener("beforehide", this.beforeHideHandler);
  },
  beforeUnmount() {
    if (this.modalEl && this.beforeHideHandler) {
      this.modalEl.removeEventListener("beforehide", this.beforeHideHandler);
    }
  },
  methods: {
    formatDuration,

    show() {
      UIkit.modal(this.$refs.viewerModal).show();
    },
    hide() {
      UIkit.modal(this.$refs.viewerModal).hide();
    },
    goFullscreen() {
      this.$refs.openseadragon.openFullscreen();
    },
    resetFilters() {
      this.brightness = 1;
      this.contrast = 1;
      this.saturation = 1;
    },
  },
};
</script>

<style scoped>
input[type="range"] {
  pointer-events: auto;
  z-index: 1001;
}

#viewer-modal {
  padding: 10px;
}

#viewer-modal-body {
  padding: 10px;
  width: 95%;
  height: 95%;
  display: flex;
  flex-direction: column;
}

#viewer-modal-header {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  margin-bottom: 0.5rem;
}

#viewer-modal-title {
  margin: 0;
  justify-self: start;
}

.scan-info {
  justify-self: center;
  font-size: 1.4rem;
  font-weight: normal;
}

.viewer-header-buttons {
  justify-self: end;
  display: flex;
  gap: 0.5rem;
}

.controlsContainer {
  display: flex;
  justify-content: center;
  gap: 1.5rem;
}

.viewer-controls {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

.viewer_container {
  flex: 1 1 auto;
  position: relative;
  overflow: hidden;
}

.reset-button {
  margin-top: 0.5rem;
}
</style>
