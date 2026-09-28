import { describe, expect, it } from "vitest";
import GalleryCard from "../../components/tabContentComponents/galleryComponents/galleryCard.vue";

function context(galleryInfo) {
  return {
    dataUri: "http://pi/api/v3/data/",
    itemData: {
      card_type: "Scan",
      path: "scan_0001",
      gallery_info: galleryInfo,
    },
  };
}

describe("gallery WSI download", () => {
  it("prefers the pyramidal BigTIFF", () => {
    const vm = context({
      stitched_tiff: "images/scan_stitched.ome.tiff",
      stitched_jpeg: "images/scan_stitched.jpg",
    });

    expect(GalleryCard.computed.downloadUrl.call(vm)).toBe(
      "http://pi/api/v3/data//scan_0001/images/scan_stitched.ome.tiff",
    );
    expect(GalleryCard.computed.downloadLabel.call(vm)).toBe("Download BigTIFF");
  });

  it("keeps old JPEG-only scans downloadable", () => {
    const vm = context({
      stitched_tiff: null,
      stitched_jpeg: "images/scan_stitched.jpg",
    });

    expect(GalleryCard.computed.downloadUrl.call(vm)).toBe(
      "http://pi/api/v3/data//scan_0001/images/scan_stitched.jpg",
    );
    expect(GalleryCard.computed.downloadLabel.call(vm)).toBe("Download JPEG");
  });
});
