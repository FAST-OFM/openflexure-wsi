/**
 * Configuration file for vue-docgen-cli.
 * Defines the components to parse, output location, ignore rules,
 * and custom template rendering logic to filter out undocumented components.
 * @module docgen-config
 */

/**
 * Target the specific component template file and extract its default export.
 * Fallback to the module itself if the default export is not present.
 * @type {Function}
 * @private
 */
const defaultComponentTemplate =
  require("vue-docgen-cli/lib/templates/component").default ||
  require("vue-docgen-cli/lib/templates/component");

module.exports = {
  /**
   * Specify Vue components location using a glob pattern.
   * @type {string}
   */
  components: "src/components/**/*.{vue,ts,js}",

  /**
   * Target directory for the generated documentation.
   * @type {string}
   */
  outDir: "../apidocs/webapp_docs",

  /**
   * List of glob patterns for files or directories to ignore during parsing.
   * @type {string[]}
   */
  ignore: ["**/node_modules/**", "**/tests/**", "**/*.md"],

  /**
   * Output all component documentation into a single file (Relative to outDir).
   * @type {string}
   */
  outFile: "components.md",

  /**
   * Function to determine the output file name for component docs.
   * Returning false prevents automatic README.md appending, enforcing the single outFile.
   *
   * @returns {boolean|string} Returns false to disable separate doc files.
   */
  getDocFileName: () => false,

  /**
   * Custom template functions to override default vue-docgen-cli rendering logic.
   * @type {Object}
   */
  templates: {
    /**
     * Custom component template renderer.
     * Filters out components that do not contain any documentation data
     * (props, events, methods, slots, description, or tags).
     *
     * @param {Object} renderedUsage - The rendered usage examples.
     * @param {Object} doc - The parsed component documentation data object.
     * @param {Object} config - The current vue-docgen configuration object.
     * @param {string} fileName - The name/path of the parsed component file.
     * @param {Array<Object>} requiresMd - Array of required markdown files associated with the component.
     * @param {Object} options - Additional rendering options.
     * @returns {string} The rendered markdown string for the component, or an empty string if skipped.
     */
    component: (renderedUsage, doc, config, fileName, requiresMd, options) => {
      // Check if the parsed component has any actual documentation data
      const hasDocs =
        (doc.props && doc.props.length > 0) ||
        (doc.events && doc.events.length > 0) ||
        (doc.methods && doc.methods.length > 0) ||
        (doc.slots && doc.slots.length > 0) ||
        doc.description ||
        (doc.tags && Object.keys(doc.tags).length > 0);

      // If it is completely empty, return an empty string to skip it entirely
      if (!hasDocs) {
        return "";
      }

      // Otherwise, pass the data back to the default component template renderer
      return defaultComponentTemplate(renderedUsage, doc, config, fileName, requiresMd, options);
    },
  },
};
