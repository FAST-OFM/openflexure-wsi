<template>
  <div ref="osdViewerContainer" class="osd-viewer-container uk-height-1-1">
    <div id="openseadragon" ref="osdContainer"></div>
  </div>
</template>

<script>
import OpenSeaDragon from "openseadragon";
import { useIntersectionObserver } from "@vueuse/core";

export default {
  name: "OpenSeadragonViewer",

  props: {
    src: {
      type: [String, Object],
      required: true,
    },
    brightness: {
      type: Number,
      required: true,
    },
    contrast: {
      type: Number,
      required: true,
    },
    saturation: {
      type: Number,
      required: true,
    },
  },

  emits: ["entering-fullscreen"],

  data: function () {
    return {
      osdViewer: null,
    };
  },

  watch: {
    src() {
      if (this.osdViewer) {
        this.loadOpenSeaDragon();
      }
    },
    brightness() {
      this.updateFilter();
    },
    contrast() {
      this.updateFilter();
    },
    saturation() {
      this.updateFilter();
    },
  },

  async mounted() {
    useIntersectionObserver(
      this.$refs.osdViewerContainer,
      ([{ isIntersecting }]) => {
        this.visibilityChanged(isIntersecting);
      },
      { threshold: 0.0, rootMargin: "500px" },
    );
  },

  beforeUnmount() {
    // Remove global signal listener to perform a gallery refresh
    if (this.osdViewer) {
      this.osdViewer.destroy();
      this.osdViewer = null;
    }
  },

  methods: {
    visibilityChanged(isVisible) {
      // adding this check to avoid error when the viewer is not yet initialized.
      if (isVisible) {
        if (!this.osdViewer && this.src) {
          this.loadOpenSeaDragon();
        }
      } else {
        // Don't destroy if viewer is in fullscreen
        if (this.osdViewer && !this.osdViewer.isFullPage()) {
          this.osdViewer.destroy();
          this.osdViewer = null;
        }
      }
    },

    async loadOpenSeaDragon() {
      if (this.osdViewer) {
        this.osdViewer.destroy();
        this.osdViewer = null;
      }
      await this.$nextTick();

      if (this.$refs.osdContainer) {
        this.$refs.osdContainer.innerHTML = "";
      }

      this.osdViewer = OpenSeaDragon({
        element: this.$refs.osdContainer,
        crossOriginPolicy: "Anonymous",
        drawer: "auto",
        preserveViewport: false,
        buildPyramid: false,
        tileSources: this.src,
        showNavigationControl: false,
        maxZoomPixelRatio: 4, // how far you can zoom in, as a ratio of pixel size
        zoomPerScroll: 1.5, // how much to zoom in per scroll wheel
        animationTime: 0.75, // speed up the animation time to make moving less floaty
        gestureSettingsMouse: {
          clickToZoom: false,
        },
        imageLoaderLimit: 4,
        maxImageCacheCount: 100,
        timeout: 30000,
      });
      // This detects if the OSD API has Context Recovery and enables it
      if (
        this.osdViewer.drawer &&
        typeof this.osdViewer.drawer.setContextRecoveryEnabled === "function"
      ) {
        this.osdViewer.drawer.setContextRecoveryEnabled(true);
      }

      this.updateFilter();
    },

    updateFilter() {
      const viewerEl = this.$refs.osdContainer;
      if (viewerEl) {
        viewerEl.style.filter = `
          brightness(${this.brightness})
          contrast(${this.contrast})
          saturate(${this.saturation})
        `;
      }
    },
    openFullscreen() {
      if (this.osdViewer) {
        // Alert the modal we are about to enter fullscreen so it can prevent closing.
        this.$emit("entering-fullscreen");
        // Wait a bit for DOM to resize
        this.$nextTick(() => {
          this.osdViewer.setFullScreen(true);
        });
      }
    },
  },
};
</script>

<style lang="less" scoped>
#openseadragon {
  width: 100%;
  height: 100%;
  background-color: black;
  z-index: 1;
}
</style>
