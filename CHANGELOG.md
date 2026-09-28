# [v3.0.0-beta1](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v3.0.0-alpha5...v3.0.0-beta1) (2026-09-01)

With this release, we move v3 from alpha to beta 1. This release has combined some essential back-end changes with improving the user experience, especially during calibration and scanning. It also sees the return of the Gallery!

* A new Gallery tab replaces the Scan List tab With significant improvements including:
  * Single captures can be saved to the gallery again.
  * Gallery back-end is now a SQL database for speed and to minimise SD card operations
  * Gallery can be filtered by data type
  * Details view or thumbnails view
  * A number of improvements to the viewer
* Huge improvements to scanning including: 
  * Lowered settling time during scanning, improving speed
  * Support for stitching two images, allowing previews to start sooner
  * Allow the user to see details of the scan settings while in progress
  * Improved stitching algorithm to reduce the likelihood of a misplaced image
* Improvements to the calibration wizard (especially on first use) including:
  * New steps for z-motor calibration and alignment
  * Improved design
  * Animations to alert the user to move the slide.
* A new camera API which:
  * Makes it easier to add new cameras
  * Better defines the capturing and streaming modes for cameras
  * Allows the Raspberry Pi HQ camera to be used without needing a custom branch (requires a settings file change)
* Continued improvement and modernisation of the webapp including:
  * A number of improvements for tablet and mobile users
  * Library modernisation including switching from VueX to Pina
  * Improved the live action log (used for long running tasks) to only show the most recent message unless the log is manually expanded
  * Improved the Logging tab to group logs together
* Global locking of the server to stop actions happening at the same time.
* Fixed autofocus frame timing so autofocus no longer overshoots.
* Added a return to centre button
* The API is now mounted at `/api/v3` for consistency with v2, plus other API changes for consistency across Connect versions
* A lot of small UI changes, from feedback during the user survey, field trips, and workshops
* A huge number of bug fixes and other irritations fixed

The following merge requests have been merged into v3:

* !523 Add labthings debug config when spinning up a server
* !564 Stack trends
* !558 Add test that stack checking agrees with at least 80% of manual labels
* !568 Rename "pixels per step" to "steps per pixel"
* !571 Make properties of JPEGSharpnessMonitor read-only
* !572 Install management server from pypi.
* !573 Add -p argument to pull_webapp for setting GitLab project ID
* !574 Change shutdown command depending on OS
* !553 ui_migration Migrate VUEX to Pinia
* !541 Choose a correlation resize factor rounded to an int
* !576 Start implementing GalleryThing
* !575 ui_migration refactor(build) reduce chuncking size <!-- codespell:ignore chuncking -->
* !577 ui_migration Clean STYLE, setup Lint, at NPM and CI
* !562 Add an API prefix
* !207 Calibrate settling times after moves
* !584 More details in bug template
* !585 Update endpoints for logs
* !567 Scale blur in simulator based on objective
* !561 Use a default_factory for dict default values
* !580 Start the UI transition from ScanList to Gallery
* !581 Bugfix/close mitt signals
* !555 Update to validating properties on write.
* !570 Enable global locking.
* !578 Record fom as other sharpness metric
* !583 Measure framerate
* !589 Allow -x in hardware tests to fail fast
* !591 Gallery fixes
* !582 Remove dev tools from production app
* !569 PNG Capture
* !587 Update code to use public `api_prefix` property
* !590 Save useful ROM data to data directory
* !556 Verify properties and actions exist before making controls and buttons.
* !592 Sharpness monitor accepting 0 as None
* !593 Log during stitch all and handle errors
* !588 ui_migration feature(unit-testing) Add isolated component testing, coverage and configurations.
* !594 Bump LabThings FastAPI version to 0.2.1
* !550 ui_migration upgrade NodeJS version 26 and configurations
* !597 ui_migration refactor(store) Remove lingering getters for origin and available...
* !598 ui_migration upgrade(openseadragon) Fixings component lifecycle for v6
* !600 Streaming modes
* !603 ui_migration Bugfix aboutContent not showing values
* !605 ui_migration bugfix(MJPEG) fix logic and lifecycle leaks at StreamDisplay, MiniStreamDisplay keeping request open in the background.
* !602 ui_migration test(store) add tests for wot, and settings
* !613 Add extra links to about
* !610 Help text for errors
* !601 Update camera API to use defined capture modes
* !615 Debug flag
* !616 Collapse action logs by default
* !617 pysangaboard version bump
* !606 Allow dots within filepaths if not trailing.
* !619 Provide an API endpoint for camera snapshots rather than relying on legacy.
* !618 Logging more info
* !604 ui_migration Smoke test VUE components
* !599 Add a toggle switch for image capture to gallery
* !623 Compile hook-inverse colours as CSS variables now components don't import the full less theme
* !624 Define custom colour style and their inverse in variables.less
* !620 Style action status message and don't show with last log.
* !614 Intended use disclaimer
* !621 Adds filtering to the gallery
* !629 Fix temp dir path for data directories in unit tests
* !627 Use padding rather than line height for button sizing
* !625 Add gallery bulk actions and sidebar
* !626 Change RangeofMotionThing to StageMeasureThing
* !634 Provide both required arguments to `move_in_image_coordinates`
* !630 Stitch 2 images
* !635 Correct docs on running server manually on Pi to include PATH
* !636 Make steps to reproduce more visible in bug template
* !632 Stop showing cancel modals on action button by default
* !638 Reduce spacing in action log error summary to prevet cut-off
* !631 Add calibration log to CSM pane of cal wizard
* !640 Reset to default option for properties
* !639 Changed docstring for capture modes
* !637 Adjust catching of InvocationCancelledError so stiching reports as cancelled
* !643 Change the classes for gallery cards information to be XxxGalleryInfo
* !641 Return more live scan details for the UI
* !646 Gallery views
* !647 Add invocation ID to log message if available
* !633 Stage direction calibrate
* !645 CSS dev docs
* !649 Ensure CSM calibration pane updates from Skip to Next on successful calibration.
* !650 Make unit tests cross OS
* !234 Store logs as dict on server, and send as json, rather than regex from str
* !651 Cal wiz illumination adjust
* !655 Action log: only show log summary once first message arrives
* !656 Capture timestamp with frame to improve autofocus
* !657 Decrease default settling time to 0.05
* !658 Add margin to checkbox to prevent overlap with text on right, remove spaces
* !654 Use min-height with clamping to preserve space without truncating early
* !660 Re-calculate time offset from CPU time to real time on each frame
* !662 Remove form tags from around position controls as it can lead to unwanted refresh
* !668 Don't warn about unstitched single captures
* !611 Return to centre button
* !670 Make control headers consistent
* !669 Set capture location from dropdown for clarity
* !678 Fix Property Mocks to not leak between tests
* !673 Back gallery with a database for speed
* !683 Scan completion reasons
* !682 Add logging about dx dy to the start of every scan
* !667 Scan live info
* !608 ui_migration feature(docs) add documentation generator for project files, and configurations
* !681 Edit Release checklist.md with testing settings
* !688 Add .gitignore for apidocs/webapp_docs
* !674 Modals no longer refresh gallery, action buttons can
* !686 Bump the version of labthings.
* !690 Pin numpy to latest before 2.5 change to behaviour
* !679 Highlight changed accordion headers
* !687 Remove second cal modal and start app-level modal via signal
* !685 Move to Ruff 0.16.x
* !689 Add buttons for viewing and downloading captures in gallery
* !680 Bugfix, allow backlash correction to be set to zero for axes.
* !648 Collapse logs by invocationID
* !684 Read stream aspect ratio on first frame, and use to allocate container size
* !677 Fix images in stack not being captured
* !692 Fix canStitch property check
* !691 Cal wiz animations
* !696 Prevent highlighting text on buttons
* !693 Add can_calibrate to Things, use in wizard
* !694 Adjust galleryViewer parameters
* !695 Components controlling alignment
* !697 Pop up blocking portrait mobile phones
* !698 Remove -rc1 from lanthings-fastapi dependency
* !700 Bump stitching version to 0.3.0
* !675 Scan info in gallery viewer
* !701 Improved microscope identification
* !644 Show a message if Vue can't start.
* !703 Add CI job for checking windows line endings
* !704 Drop default overlap to 0.35
* !702 beta1 version bump and changelog
* !706 Fallback page
* !707 Skip picamera logs in logging tab
* !708 Await things which can be calibrated
* !705 Stable identifier in grouping logs, use calling action for group title
* !709 Bump Stitching to v0.3.1 

# [v3.0.0-alpha5](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v3.0.0-alpha4...v3.0.0-alpha5) (2026-04-08)

The fifth alpha release of the server. This release has been a huge amount of work and brings us significantly closer to a semi-stable beta release. Some highlights are:


* Move from LabThings Dependencies to ThingSlots. This has allowed for:
  * Much easier communication between Things
  * Static type checking
  * Simpler action creation.
* Created ScanWorkflows, a backend change that allows switching scan methods. Enabling new types of scanning:
  * Raster Scan
  * Snake Scan
  * CChip Scan (experimental, requires a configuration change to use)
* Transition front end from the end of life Vue2 to Vue3 which is in active development!
* Improvements to the simulation microscope including more simulation samples and camera options. Speeding up development.
* Improved testing, including better defined testing environments, separation of integration and unit tests, and tests passing on Windows
* OpenCV camera switching. Enabling changing between cameras in settings when in manual configuration.
* "Jogging" movement. The microscope can now move continuously when an arrow key is held.
* Improved server side UI specification. This brings us closer to "Things" being able to define their own tab, which will soon enable plugins in v3.
* A user interface for recentring and range of motion.
* Improved fallback server interface.
* Backlash correction in scanning.
* A huge number of other bug fixes and improvements.


The following merge requests have been merged into v3:

* !450 Update to be compatible with labthings-fastapi 0.0.12/0.0.13
* !451 Start to remove deps
* !452 Removing dependencies from CSM, autofocus, and stage_measure
* !453 Final dependency removals
* !454 Use new LabThingsTestEnv thoughout tests <!-- codespell:ignore thoughout -->
* !455 Type fixes
* !456 Proper integration tests
* !457 Fix (most) unit tests on Windows
* !458 Simulation Camera Improvements
* !460 Bugfix, stop saving scans into log folder
* !461 Background detector things
* !462 Implement ScanWorkflows allowing multiple scanning methods
* !464 SnakeScan as a scan planner
* !469 More workflow layers
* !465 Add raster scan, with generic typing to allow subclassed settings
* !468 Illumination Thing
* !473 HTTP API docs
* !479 Vue2-3 migration.
* !480 Tidy optional deps
* !478 Increase dz range to 400 to 3000
* !472 Manually handle out of storage and JPEG too big errors
* !485 Restore CSM stream pos to right of buttons
* !486 Add URL to wiki if Sangaboard firmware out of date
* !475 Grey out read only properties in webapp
* !484 Reduce the blur for a given z movement in simulation, to improve autofocus.
* !489 Bump ESLint from unsported version to version 9
* !483 CSM modal shows stream
* !476 Generalised Jogging
* !477 Optional options in UI property control become dropdown
* !482 Add recentre and ROM test buttons to stage settings
* !495 Add --max-warnings=0 back to eslint check. This was accidentally dropped when moving from vue-cli-service
* !490 Magnification selection in simulator by cropping canvas based on objective property
* !497 Re-enable prettier
* !496 Update release checklist from last alpha's experience
* !494 Use lt.property/setting validatiors to set numerical input range and step <!-- codespell:ignore validatiors -->
* !498 Update logging page formatting.
* !499 Use Invocation errors for expected errors.
* !500 Add a fallback page template.
* !505 Remove references to old GPU preview feature.
* !506 Webapp cleanup
* !504 Use own pagination consistently rather than vue-pagination-next
* !502 Add spellchecking for camelCase, PascalCase, and kebab-case
* !501 Scan logs
* !508 Allow 0 as a setting in the webapp
* !493 Split SmartStackParams into focus, capture, stack params
* !487 Add functionality to switch OpenCV camera without restarting the server.
* !514 Move to headless opencv
* !513 More developer documnetation <!-- codespell:ignore documnetation -->
* !510 Change pointer event names for clarity
* !509 Backlash correction in base stage class
* !517 Allow histology scans with equal dx and dy
* !516 Do less coersion for stacks now labthings validates settings. <!-- codespell:ignore coersion -->
* !519 Metadata testing
* !503 Create and mount application level data directory rather than just a scan directory
* !491 Decrease target brightness for both cameras as its pre-gamma
* !525 Align buttons in confirmation modal
* !526 Fix spellings found as codespell updates
* !518 Add limits to background detect and settling time settings
* !527 Update bug template to include table or configuration, more detail about how...
* !532 Reorder Bug.md expected and actual behaviour with correct comments
* !531 Don't tab to spinners in webapp
* !524 Improve Validation Checks
* !535 Remove uk-input from checkbox
* !530 Increase number of UI elements that can be specified in the server
* !442 Handle scan settings, then change stream, then pre-scan procedure when scanning
* !533 Stop closing seadragon when going full screen
* !529 Move more development information to the dev docs to minimise repitition and getting out of sync <!-- codespell:ignore repitition -->
* !474 CChip scan workflow
* !544 Update codespellrc to ignore ./git folder, not extension
* !545 Update camera cal instructions to look through empty area if possible
* !540 Flush more frames and use a request when doing exposure time
* !542 Close calibration with confirmation
* !546 Update path to ofm server and config file in docs
* !547 Explicit windows check before sigkill for mypy
* !536 Split up slide scan tab and create a generic action tab (attempt 2)
* !548 Word break long lines in notifications
* !552 Move from posix path to os path
* !554 Move smart stacking into a mixin so it can be re-used in other workflows <!-- codespell:ignore "re-used" -->
* !551 Sort scans by created time
* !538 Update dependency to labthings 0.1.0
* !557 Prep for v3.0.0 alpha5 
* !560 Bump stitching version

# [v3.0.0-alpha4](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v3.0.0-alpha3...v3.0.0-alpha4) (2025-12-15)

The fourth alpha release of the server. This release has concentrated on colour reproduction, scanning, and the structure of the web-app.

* Improved PiCamera tuning and colour reproduction
  * Algorithm for white-balance calculation improved. Images are grey not yellow.
  * Improved colour correction matrices
  * Ship our own custom picamera Tuning files, and improve handling of updating this data
  * Add contrast and brightness sliders into scan viewer.
* Scanning improvements
  * Capture extra images on the boundary between sample and background to robustly capture the edges of the sample
  * Add new default background detect method that works on localised image variation not global colour
  * Add extra checks into smart stack for checking if stack is successful
* Stage measuring
  * Automatic range of motion measurements - Not yet exposed in UI
  * A new recentre algorithm - Not yet exposed in UI
* Improvements to the webapp
  * Significant modernisation of the webapp build system
  * UI customises to available Things - This allows different configurations such as manual microscopes with no stage!
  * Overhaul of the code that runs the Calibration Wizard. This will make it easier to add new calibration steps in future
  * Add pagination to scan list

The following merge requests have been merged into v3:

* !404 Fix jitter at specific screen sizes during scan
* !406 Strip new lines when logging stitching
* !411 Improve smart spiral scan planner to better capture the edge of scamples
* !414 Set colour correction to interpolate value
* !409 Colour sliders in seadragon viewer
* !413 Fix sync button to emit click on click event.
* !408 Scan tab show scan duration
* !416 Add section to Picamera tests README on keeping branches up to date
* !412 Camera metadata with basic data
* !335 Adding automatic range of motion measurements
* !415 Refactor Calibration Wizard
* !419 Improve simulation frame rate
* !420 Specify config patches rather than full config files in settings
* !417 Set Colour Gains from lens shading tables
* !422 Enable JS formatting checks in CI, reformat to match
* !425 Stricter rules on pytest.raises, requiring match for generic error types
* !424 De-duplicate HTML for tab content, add way for tab to request scroll to top
* !421 Add pagination to scan list
* !418 Update the picamera tuning file utilities to not modify the dictionaries in place
* !428 Split up the action button methods for monitoring and starting a task
* !430 Local storage in store
* !431 Update js toolchain
* !426 Stop reading scan_data.json for ongoing scan.
* !433 Skip buttons on calibration wizard
* !436 A matrix display component with copy button that copys as code. <!-- codespell:ignore copys -->
* !410 Ship our own default tuning files
* !435 UI customises to available Things
* !429 Recentre using RangeOfMotionThing methods
* !440 Resolve "Release Checklist"
* !437 Consolidate action buton to just a button <!-- codespell:ignore buton -->
* !439 Configuration for manual microscope
* !444 Visit secondary locations first
* !447 Log an error if the sangaboard firmware is below v1.0.4
* !434 Improve the stability of scanning
* !438 Formalise import sorting rules and enforce
* !448 Bump required stitching version to 0.2.1
* !449 Prep for v3.0.0-alpha4

# [v3.0.0-alpha3](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v3.0.0-alpha2...v3.0.0-alpha3) (2025-10-10)

The third alpha release of the server. The focus has been on stability and documentation. Some highlights are:

* Experimental support for the High Quality Raspberry Pi Camera.
* UI improvements including:
  * Updating property controls to show visibly when they are set
  * Reducing the number of modal confirmations, and improving formatting for the remaining
  * Renaming navigate tab to control tab
  * Improving consistency of which buttons are primary, and that disabled displays in dark mode
  * Reloading settings more in the UI when they change in server (ongoing)
  * Fixing a number of caching issues in the UI.
* Calibration modal improvements:
  * A button to reopen the modal
  * An interface to focus the sample before camera stage mapping.
* The user can now cancel the preview thread (waiting for background processes)
* A number of improved error messages and logs.
* A number of changes to the documentation and the addition of walkthroughs to help new devs set up their environment.

The following merge requests have been merged into v3:

* !355 Changed navigate tab to control tab
* !356 No longer use LabThings-FastAPI dev dependencies
* !358 Add flake8 bugbear checks
* !360 Button added to launch calibration menu manually
* !359 Allow preview to cancel!
* !357 Update gitlab ci file
* !365 Adjust z height in calibration modal
* !361 Request no cache
* !366 Change all CameraDeps to CameraClients
* !367 Typehints and better variable names for autofocus
* !368 Check static files exist or throw a useful error
* !351 Custom sigkill error
* !373 Fix picamera default tuning being updated by calibrations, add tests
* !374 Prevent broken frames ending scans
* !377 Add fullscreen toggle button.
* !376 Tweak a few error types.
* !375 Stop reporting uvicorn.error in log file unless log it is an error
* !381 Update BaseModels to use ConfigDict rather than directly use a dictionary
* !379 Test server startup
* !380 Make simulation image wrap to prevent errors when moving out of range.
* !378 Deduplicating camera functionality
* !382 Enable ruff checks for type annotations
* !383 Improve property control UI
* !371 Change neighbour ratio to dx and dy
* !384 Format values in value confirmation modal to prevent overflow.
* !385 Reload background detect settings when tab opened
* !387 Start swapping streams when checking background
* !394 Allow both HQ camera or PiCamera v2
* !395 Enable all lint checkers, remove ruff-increased-checking job!
* !386 Action buttons can show as disabled in dark mode
* !399 Resolve "Make Walkthroughs for Software Installation"
* !398 Fix error trying to purge empty scans if current scan has no images
* !397 Scan list improvements
* !401 Simulate a rectangular sample
* !402 Clean up primary vs secondary buttons
* !400 Verify stack params at start of scan main loop
* !403 Ce disable
* !390 Improve documentation for simulation server
* !405 Bump version for alpha3 and update changelog 

# [v3.0.0-alpha2](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v3.0.0-alpha1...v3.0.0-alpha2) (2025-08-08)

This is the second alpha release the v3 server, the main focus has been higher resolution scans with more reliable focus. There have been many other changes, some highlights are:

* Capture in Scanning increased from 0.5MP to 2MP
* Using updated stitching which is significantly faster.
* Scanning uses a "smart stacking" that camptures images until it has found the sharpest one. Removing the need to perform autofocus, and improving the focus reliability.
* Server version is saved into image metadata
* Improvements to settings in LabThings-FastAPI so settings persist more reliably
* Improvements to memory management, process, and directory management during scanning
* UI improvements including:
  * Direct download of stitched image
  * Stitching can be cancelled from the UI
  * Stitch all scans button in the UI to stitch any scans that are not already stitched
  * The log display pauses autoscroll on mouse hover
  * More consistent updating of scan tab
  * Better description of server version information
* Picamera and Sangaboard code are now directly in the repository, in heriting from BaseCamera and BaseStage class. Allowing core functionality to be added to BaseStage and Base Camera.
* Other underlying changed that will make customisation easier in the future.
* Significantly increase in code testing

The following merge requests have been merged into v3:

* !290 Enable more lint checkers
* !288 Remove numerical input spinners for x,y,z settings in Firefox
* !295 Update issue templates
* !298 Git ignore openflexure/ dir
* !297 Add a timeout when shutting down the server.
* !300 Automatically create HTML coverage report each time PyTest runs
* !302 Correct spelling of Sangaboard
* !303 Remove the overwriting of internal globals, requires removing future annoations <!-- codespell:ignore annoations -->
* !251 2MP from 8MP capturing
* !271 A stack that tests whether the sharpest image is towards the middle
* !294 Trying to make a distinction between file directory operations for scans and scanning code
* !308 Replace semi colon with double ampersand in package.json
* !305 Allow downloading just the stitched image, and don't zip DZIs
* !309 Combine uniquness and length checks <!-- codespell:ignore uniquness -->
* !310 Remove test Thing that is is never used.
* !312 Allow stiching to be cancelled after a scan complete
* !311 Move SangaboardThing into repo
* !315 Fix artifact path in CI for webapp
* !313 Remove out of date CaptureThing from Simulation and Stub config
* !314 Remove old unmaintained components
* !296 Update Thing Settings and import statments for LabThings-FastAPI 0.0.10 <!-- codespell:ignore statments -->
* !319 Python integration tests
* !316 Stop streams to ensure that server exits gracefully.
* !320 Better docstrings
* !322 Update README to point at CONTRIBUTING and make contributing link to site,...
* !324 Fix example docstring example in CONTRIBUTING.md
* !325 Test logging
* !326 Add camera-stage-mapping compliant functions to BaseStage and BaseCamera.
* !321 Update setting manager
* !323 Merge settings and system control
* !328 Update simulation canvas to have colour so it works with background detect.
* !330 Stitch all scans button, and refresh scan tab when closing modals
* !327 Refactor background detect to allow switching algorithms
* !332 Make log display pause on hover
* !333 Bump LabThings-Fastapi to 0.0.11
* !336 Remove zenodo scripts and job
* !334 Stop exposure walking on camera reload, reset CCM on full calibration
* !339 Remove auto-recentre stage
* !340 Remove class from action button
* !337 Report Picamera tests in CI
* !341 Prepend images with img_
* !343 Update .coveragerc to get 2 decimal places for coverage
* !331 Dynamic UI for camera and background detect settings
* !345 Change "settings" to "configure" in background detect tab
* !346 Make scan directory manager thread safe
* !344 Scanning in simulation
* !338 Fix movements axes
* !347 Pull webapp
* !342 Break up SmartScan further so it can be tested
* !353 Speed up `tests/test_stage.py` significantly
* !352 Update to stitching 0.2
* !354 Final v3 alpha2 things


# [v3.0.0-alpha1](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.11.0...v3.0.0-alpha1) (2025-06-09)

This is the first alpha release of an almost total overhaul of the server, and the underlying LabThings framework. Some highlights are:

* Moved to underlying architecture based on [labthings-fastapi](https://github.com/labthings/labthings-fastapi). This allows each component of the microscope to be a "Thing", which can interact with each other.
* Current "Things" are camera, stage, autofocus, smart_scan background_detect, auto_recentre_stage, capture, settings_manager, system_control and test
* Overhaul of scanning, removing the predetermined, rectangular area in favour of exploring the sample indefinitely by appending neighbouring xy sites after each capture
* Background detect Thing optionally uses the colour distribution of a user-set "background" image to determine if the field of view is sample or background, and plan future scanning accordingly
* Stitching from openflexure-stitching now part of the scanning protocol, displaying a live updating preview of the current scan, and optionally producing a high res stitched JPEG, DZI and/or TIFF at the end of the scan
* A DZI viewer in the scan tab, allowing stitched images to be opened from within the GUI
* Webapp updates - scan tab added showing thumbnail preview + image count + scan times, gallery removed, tour removed, power tab added,
* Updated to numpy 2
* Automatically delete empty scans

V3 was started in a branch (MR !159)

The following merge requests have been merged into v3:

* !162 Scanning and recentring
* !163 Web app compatibility with v3 API
* !164 Modal progress dialog for TaskSubmitter
* !166 Pseudo spiral scan path and fixed dx-dy inversion
* !167 Restored old log path
* !168 Refactor task submitter
* !169 Run `openflexure-stitch` on the server
* !170 custom scan name from gui
* !171 Make it possible to manually save all settings to disk
* !172 Updates based on feedback from Rwanda trip
* !173 Autofocus efficiency
* !174 Parallel acquisition and moves
* !175 Improvements to autofocus and stitching in `smart_scan`.
* !176 Replace tasksubmitter with action-button
* !181 Add configurability to the server
* !191 Migrate to new Blob type and update imports
* !192 Formal Protocol for camera and stage interfaces
* !194 Add more unit tests and fix CI
* !226 Split up ruff format and lint
* !225 Rename exposure (with limit)
* !232 Purge all windows line endings and add check to formatter.
* !231 Reorder tabs
* !211 Use the UV channels only to determine if an area is background or sample
* !228 Stop uvicorn.run disabling existing loggers
* !227 CSM always returns to start on error
* !236 Inverse colours
* !239 Scanning cleanup
* !240 Refactor scanning
* !241 Add some extra rules to ruff, also add a more complete ruff config that fails
* !242 Further scan refactor
* !245 Add a scan path planner that prioritises shorter moves
* !247 Looping autofocus at the start of the scan
* !249 Remove focus_change_acceptable test
* !244 Remove correlation only stitching, now covered by preview stitch
* !250 Thumbnails and accurate durations in scan-list tab
* !253 Regex to count captured images
* !238 Disable tour
* !246 z stacking Thing
* !248 Cleanup zipping
* !255 Remove autofocus test
* !256 New comments on scanning
* !259 Update scan data JSON at end of scan
* !260 Scan tab updates
* !258 Use NumPy v2 and new wheel for opencv
* !230 Lazier regex for finding logs with square brackets
* !263 Clean up of serve_static_files
* !265 Update serve static files
* !267 Update theme.less to prevent automatic changed colour of uk-alert-success
* !268 GUI clean up - button cases, features tab and redo tour
* !269 Action buttons raise an Error from the last message in the log for the Actio
* !257 Added purge_empty_scans() method
* !270 Import openflexure-stitching from pip
* !272 Settling time as global, settle before taking first image in stack
* !261 CSM info in webapp tab
* !275 GUI shutdown clean up
* !276 Ui fixes
* !277 Fix error in purge_empty_scans if the `images` dir hadn't been created
* !266 Gui dzi viewer
* !278 update labthings-picamera to most recent release
* !279 Simulation fixes
* !281 Change from building with node 15 to node 18
* !282 Defocus and realign simulation axes
* !280 Custom `from_index()` function in webapp for backwards compatibility before javascrpt `at()`
* !284 Update labthings-api dep and also the version number for alpha release
* !286 Use same version number for server and webapp, add CI check
* !285 Swapped the order of CSM so y axis is completed first 
* !283 Add scan directory to json config 
* !287 Update depedencies and changelog prior to v3.0.0-alpha1 release <!-- codespell:ignore depedencies -->

# [v2.11.0](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.10.1...v2.11.0) (2022-08-08)
## New features
* Background detection can now be used in scans, if you have the background-detect extension. ([!153](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/153))
* Support for Sangaboard firmware v1, via updated `sangaboard` dependency ([!154](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/154))
* Fixed links in the OpenAPI documentation describing actions etc. ([!155](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/154))

# [v2.10.1](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.10.0...v2.10.1) (2022-01-17)
## Bug fixes
* Fixed a typo in the serialisation of numpy arrays (#243) ([!148](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/148))
* Made it possible again to track sharpness while the stage is not moving.  This changed when the extra thread was removed from `JPEGSharpnessMonitor`. ([!149](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/149))

## Minor improvements
* Added a function to measure settling time after a Z move ([!149](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/149))
* Bumped some INFO logging statements to DEBUG to de-clutter the logs. ([!151](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/149))

# [v2.10.0](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.10.0b2...v2.10.0) (2021-08-11)
## Documentation improvements
* Many improvements to the generated OpenAPI documentation, resulting in it now validating. ([!133](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/133))
* Added a user guide and fixed readthedocs.  Linked to the API documentation, and store it on the build server with releases. ([!140](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/140))

## Developer changes
* The web app is now in a top-level directory ([!122](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/122))
* OpenAPI descriptions are now easier to download from a merge request. ([!139](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/139))

## Minor bug fixes and improvements
* Removed vestigial error handling code that is now dealt with by LabThings. ([!137](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/137))
* taskSubmitter buttons in the web app now handle errors more robustly.  This fixes a bug where the progress bar would spin indefinitely.  ([!138](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/138))
* Smart stack controls now only appear if the plugin is installed.  ([!142](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/142))
* Javascript dependencies are updated for security.  ([!141](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/141))

# [v2.10.0b2](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.10.0b1...v2.10.0b2) (2021-08-11)
## New features
* Added support for ImJoy plugins ([!120](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/120))
* Added a setting to disable the gallery ([!120](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/120))
* Scan parameters are now remembered in the "capture" pane ([!132](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/132))
* The Capture pane now contains controls for the smart stack plugin, if installed (([!134](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/134)))

## Developer changes
* CI now fails if `Pipfile.lock` is out of date ([!131](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/131))
* The Thing Description and OpenAPI documentation are now valid, and much improved ([!133](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/133))
* Pytest XML reports are now recorded in the CI pipeline ([!135](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/135))
* The application is now packaged for every commit on `master` ([!136](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/136))

# [v2.10.0b0](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.10.0b0...v2.10.0b1) (2021-05-26)
## Minor bug fixes and improvements
* Fixed some installation issues with updated Python dependencies ([!129](https://gitlab.com/openflexure/openflexure-microscope-server/-/merge_requests/129))

# [v2.10.0b0](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.9.3...v2.10.0b0) (2021-05-18)

## New features
* Improved auto gain, white balance, and lens shading table correction (!119).  Fixes #191.
  The old auto calibration code has been replaced by a slower, more manual method that is more reliable.
  Individual parts of the calibration can be run independently (auto gain/exposure, auto white balance, lens shading).
  This should also improve the experience when using e.g. fluorescence imaging modes.

## Developer changes
* Build instructions were updated (!115)
* We've switched from `poetry` to `pipenv` to solve dependency management issues on the Raspberry Pi (!124).  See the updated 
  repository README for a full breakdown of what configuration information goes where.  Closes #218.
* Improved handling of stream settings and origin override (!125, !116, !117)
  This fixes a longstanding irritation when developing on localhost using `npm run serve`, where it was necessary to
  enter the API origin address every time the page was refreshed, and then manually re-enable the web stream.  Both
  these issues are now fixed.
  * The API origin override field in the "dev tools" pane now remembers its value
  * The API origin can be overridden using the URL
  * Stream settings are now remembered in local storage

## Minor bug fixes and improvements
* Better error handling for picamerax (!86)
* Add coverage report to mypy (!110)
* `MissingStage` can now be explicitly selected in microscope configuration (!112)
* The first-run "tour" placement is improved and shouldn't block the interface any more (!114)
  * Fix placement of tour messages for icons ([afbb716](https://gitlab.com/openflexure/openflexure-microscope-server/commit/afbb716))
  * This closes #199 and helps with #193
* The IHI interface now explicitly states scan style should be raster (!113)
* Absolute moves are now fixed (!126), closing #220, #221, and #222.
* Fixed a typing error with the camera stage mapping matrix and `numpy` 1.20 (!127).
* Scans now have a minimum dimension of 1 in each axis, which avoids dividing by zero (!123)


# [v2.9.3](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.9.2...v2.9.3) (2020-12-14)

- Fix #194 ([0592d31](https://gitlab.com/openflexure/openflexure-microscope-server/commit/0592d31)), closes [#194](https://gitlab.com/openflexure/openflexure-microscope-server/issues/194)

# [v2.9.2](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.9.1...v2.9.2) (2020-12-09)

- Added more Advanced bitrate controls ([d2488c5](https://gitlab.com/openflexure/openflexure-microscope-server/commit/d2488c5))
- Watch for broken frames using JPEG end bytes, and log error ([6838038](https://gitlab.com/openflexure/openflexure-microscope-server/commit/6838038))

# [v2.9.1](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.9.0...v2.9.1) (2020-12-07)

- Server: Added mjpeg bitrate to settings ([3e2f876](https://gitlab.com/openflexure/openflexure-microscope-server/commit/3e2f876))
- JS Client: Replaced MJPEG quality setting with MJPEG bitrate ([42e0dfd](https://gitlab.com/openflexure/openflexure-microscope-server/commit/42e0dfd))

# [v2.9.0](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.8.0...v2.9.0) (2020-12-07)

## Developer changes

### Extension loader

- **Updated default extensions to subclass structure ([8d0759e](https://gitlab.com/openflexure/openflexure-microscope-server/commit/8d0759e))**
  - Updated documentation to subclass-based extensions ([bacc111](https://gitlab.com/openflexure/openflexure-microscope-server/commit/bacc111))
  - Added type information to LABTHINGS_EXTENSIONS ([b8354d3](https://gitlab.com/openflexure/openflexure-microscope-server/commit/b8354d3))
  - Clarified LABTHINGS_EXTENSIONS in docs ([f849afd](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f849afd))

### Testing

- Added basic unit tests of non-integrated functions ([5137d1b](https://gitlab.com/openflexure/openflexure-microscope-server/commit/5137d1b))

### Static analysis

- Added extra type hints ([7ba3f44](https://gitlab.com/openflexure/openflexure-microscope-server/commit/7ba3f44))
- Added numpy types ([63b633b](https://gitlab.com/openflexure/openflexure-microscope-server/commit/63b633b))
- Added type hints to CSM extension ([311366c](https://gitlab.com/openflexure/openflexure-microscope-server/commit/311366c))
- Fixed types in move_rel method ([ac667c3](https://gitlab.com/openflexure/openflexure-microscope-server/commit/ac667c3))
- Static type analysis ([7866ec0](https://gitlab.com/openflexure/openflexure-microscope-server/commit/7866ec0))
- Stricter runtime type checks ([f2a2d88](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f2a2d88))

### Documentation

- Added better developer notes ([a3b1b8a](https://gitlab.com/openflexure/openflexure-microscope-server/commit/a3b1b8a))
- Added changelog generator ([de6dbe4](https://gitlab.com/openflexure/openflexure-microscope-server/commit/de6dbe4))

### CI/CD

- Added eslint and cache to CI ([f5012cd](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f5012cd))
- Added job explanation comments ([c1e17de](https://gitlab.com/openflexure/openflexure-microscope-server/commit/c1e17de))
- Added poetry to script environment ([3814e7d](https://gitlab.com/openflexure/openflexure-microscope-server/commit/3814e7d))
- Allow code quality jobs to retry ([f15a5c7](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f15a5c7))
- Fixed JS artifact path ([3a90a42](https://gitlab.com/openflexure/openflexure-microscope-server/commit/3a90a42))

## API functionality

- Added API route to convert LST to PNG ([4d40e81](https://gitlab.com/openflexure/openflexure-microscope-server/commit/4d40e81))

## JS Client

### New functionality

- Added log level filter ([4d1d0a1](https://gitlab.com/openflexure/openflexure-microscope-server/commit/4d1d0a1))
- Added options to invert navigation steps ([d49b34e](https://gitlab.com/openflexure/openflexure-microscope-server/commit/d49b34e))
- Added backlash and settle time settings ([c0fcd22](https://gitlab.com/openflexure/openflexure-microscope-server/commit/c0fcd22))
- Added stream and capture quality settings ([0f7cecb](https://gitlab.com/openflexure/openflexure-microscope-server/commit/0f7cecb))

### Fixes

- Fixed global state and scrolltotop ([ef9f257](https://gitlab.com/openflexure/openflexure-microscope-server/commit/ef9f257))
- Fixed individual captures creating 'undefined' dataset ([213dec3](https://gitlab.com/openflexure/openflexure-microscope-server/commit/213dec3))
- Fixed onError propagation ([08f6532](https://gitlab.com/openflexure/openflexure-microscope-server/commit/08f6532))
- Fixed onScanError ([1de6b2a](https://gitlab.com/openflexure/openflexure-microscope-server/commit/1de6b2a))
- globalUpdateCaptures after scan ([599c315](https://gitlab.com/openflexure/openflexure-microscope-server/commit/599c315))
- Handle missing this.$refs.textboxKey ref ([9d04842](https://gitlab.com/openflexure/openflexure-microscope-server/commit/9d04842))
- Fixed IHI tab icon duplication bug ([e17366d](https://gitlab.com/openflexure/openflexure-microscope-server/commit/e17366d))

### Design/style

- Added custom h4 formatting ([4ba4403](https://gitlab.com/openflexure/openflexure-microscope-server/commit/4ba4403))
- Rearranged element layout ([455b868](https://gitlab.com/openflexure/openflexure-microscope-server/commit/455b868))
- Rearranged settings and added LST download ([f0a3127](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f0a3127))

### Internal cleanup

- Common watch format ([3e783c5](https://gitlab.com/openflexure/openflexure-microscope-server/commit/3e783c5))
- Removed unneeded plugin:vue/essential ([358d441](https://gitlab.com/openflexure/openflexure-microscope-server/commit/358d441))
- Removed unused webcomponent support ([f187a3a](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f187a3a))
- - Moved backlash settings ([a2f8158](https://gitlab.com/openflexure/openflexure-microscope-server/commit/a2f8158))

## Server

### Features

- Added API route to convert LST to PNG ([4d40e81](https://gitlab.com/openflexure/openflexure-microscope-server/commit/4d40e81))
  - Return LST as a file ([aba3eb3](https://gitlab.com/openflexure/openflexure-microscope-server/commit/aba3eb3))

### Fixes

- Fixed "classic" autofocus 'PiCameraStreamer' object has no attribute 'annotate_text' error ([58b2967](https://gitlab.com/openflexure/openflexure-microscope-server/commit/58b2967))
- Fixed broken dataset rendering if key exists but is empty ([fd42e2e](https://gitlab.com/openflexure/openflexure-microscope-server/commit/fd42e2e))
- Fixed camera settings read ([25d3b15](https://gitlab.com/openflexure/openflexure-microscope-server/commit/25d3b15))
- Fixed get_locations method reference ([d288484](https://gitlab.com/openflexure/openflexure-microscope-server/commit/d288484))
- Fixed SangaDeltaStage type annotations ([9659c45](https://gitlab.com/openflexure/openflexure-microscope-server/commit/9659c45))
- Fixed tile method reference ([18a0eed](https://gitlab.com/openflexure/openflexure-microscope-server/commit/18a0eed))
- Handle missing endpoints ([bc39277](https://gitlab.com/openflexure/openflexure-microscope-server/commit/bc39277))

### Internal API changes

- Deprecated camera `start_worker` and `get_frame` methods
  - Added `start_worker` and `get_frame` aliases for compatibility ([bc9b80d](https://gitlab.com/openflexure/openflexure-microscope-server/commit/bc9b80d))
- Replace top-level actions View with builtin LabThings ([421a2e3](https://gitlab.com/openflexure/openflexure-microscope-server/commit/421a2e3))
- Updated to LabThings 1.2.1 ([249e301](https://gitlab.com/openflexure/openflexure-microscope-server/commit/249e301))

### Internal cleanup

- **Remove separate camera stream thread ([021745d](https://gitlab.com/openflexure/openflexure-microscope-server/commit/021745d))**
- Cleaned up code layout ([da62126](https://gitlab.com/openflexure/openflexure-microscope-server/commit/da62126))
- Cleaned up main app setup ([e464086](https://gitlab.com/openflexure/openflexure-microscope-server/commit/e464086))
- Cleaned up store and removed unused FoV setting ([a1ae947](https://gitlab.com/openflexure/openflexure-microscope-server/commit/a1ae947))
- Code formatting ([dd81640](https://gitlab.com/openflexure/openflexure-microscope-server/commit/dd81640))
- Fixed unused imports ([b2192b2](https://gitlab.com/openflexure/openflexure-microscope-server/commit/b2192b2))
- Integrated CSMExtension ([9203545](https://gitlab.com/openflexure/openflexure-microscope-server/commit/9203545))
- Moved frames_iterator scope ([3058c67](https://gitlab.com/openflexure/openflexure-microscope-server/commit/3058c67))
- Moved gen() into streams.py ([c9c29a7](https://gitlab.com/openflexure/openflexure-microscope-server/commit/c9c29a7))
- Only use CompositeLock for microscope lock ([e433a89](https://gitlab.com/openflexure/openflexure-microscope-server/commit/e433a89))
- Reduced logging level of some mock camera notices ([8eff136](https://gitlab.com/openflexure/openflexure-microscope-server/commit/8eff136))
- Removed old console logs ([2e34722](https://gitlab.com/openflexure/openflexure-microscope-server/commit/2e34722))
- Removed old picamera_lst_path attribute ([85f77fa](https://gitlab.com/openflexure/openflexure-microscope-server/commit/85f77fa))
- Removed pointless abstract method implementations ([6d1f019](https://gitlab.com/openflexure/openflexure-microscope-server/commit/6d1f019))
- Removed unused pynpm package ([6819ded](https://gitlab.com/openflexure/openflexure-microscope-server/commit/6819ded))
- Tweaked deprecation warnings ([a844efc](https://gitlab.com/openflexure/openflexure-microscope-server/commit/a844efc))
- Updated dependencies ([3c3ecd7](https://gitlab.com/openflexure/openflexure-microscope-server/commit/3c3ecd7))

# [2.8.0](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.8.0-beta.3b...v2.8.0) (2020-11-16)

- 2.8.0 ([729f101](https://gitlab.com/openflexure/openflexure-microscope-server/commit/729f101))
- Added documentation comments ([3eb2aa8](https://gitlab.com/openflexure/openflexure-microscope-server/commit/3eb2aa8))
- Added frame iterator explanation comment ([90ccd0b](https://gitlab.com/openflexure/openflexure-microscope-server/commit/90ccd0b))
- Added use_video_port argument back to array() ([f51d4ff](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f51d4ff))
- Move stream frame management into FrameStream class ([f765540](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f765540))

# [2.8.0-beta.3b](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.8.0-beta.3...v2.8.0-beta.3b) (2020-11-13)

- 2.8.0-beta.3 ([73eda10](https://gitlab.com/openflexure/openflexure-microscope-server/commit/73eda10))
- Add pre-commit ([5119e2c](https://gitlab.com/openflexure/openflexure-microscope-server/commit/5119e2c))
- Added full TileScanArgs schema ([3451b8a](https://gitlab.com/openflexure/openflexure-microscope-server/commit/3451b8a))
- Added note on manually running pre-commit ([36e837d](https://gitlab.com/openflexure/openflexure-microscope-server/commit/36e837d))
- Added note on pre-commit ([0647a03](https://gitlab.com/openflexure/openflexure-microscope-server/commit/0647a03))
- Added rescue to Poetry scripts ([1f0b90c](https://gitlab.com/openflexure/openflexure-microscope-server/commit/1f0b90c))
- Cleaned up Capture schemas ([84dcf4f](https://gitlab.com/openflexure/openflexure-microscope-server/commit/84dcf4f))
- Data-driven tab layout ([c645804](https://gitlab.com/openflexure/openflexure-microscope-server/commit/c645804))
- Extra comments ([c48c6d8](https://gitlab.com/openflexure/openflexure-microscope-server/commit/c48c6d8))
- Formatting ([6644819](https://gitlab.com/openflexure/openflexure-microscope-server/commit/6644819))
- Improved args schema for captures ([f681800](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f681800))
- Moved piexif into captures submodule ([7c450b6](https://gitlab.com/openflexure/openflexure-microscope-server/commit/7c450b6))
- Moved PyLint config back to pyproject.toml ([527bfeb](https://gitlab.com/openflexure/openflexure-microscope-server/commit/527bfeb))
- Removed default JSON files and submoduled JSON encoder class ([4a9d1c5](https://gitlab.com/openflexure/openflexure-microscope-server/commit/4a9d1c5))
- Removed duplicated code ([004ba0b](https://gitlab.com/openflexure/openflexure-microscope-server/commit/004ba0b))
- Removed now-redundant TODO comments ([c0f9f34](https://gitlab.com/openflexure/openflexure-microscope-server/commit/c0f9f34))
- Removed old JSONResponse class ([b350f62](https://gitlab.com/openflexure/openflexure-microscope-server/commit/b350f62))
- Removed pre-commit from Poetry dependencies ([00d30d8](https://gitlab.com/openflexure/openflexure-microscope-server/commit/00d30d8))
- Removed PyLint from pre-commit ([cb4afba](https://gitlab.com/openflexure/openflexure-microscope-server/commit/cb4afba))
- Removed unused files from root ([e25c23c](https://gitlab.com/openflexure/openflexure-microscope-server/commit/e25c23c))
- Reverted logging style ([6fb61e1](https://gitlab.com/openflexure/openflexure-microscope-server/commit/6fb61e1))
- Style and linting ([f4b123c](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f4b123c))
- Updated all log strings to new format ([9f52521](https://gitlab.com/openflexure/openflexure-microscope-server/commit/9f52521))
- Updated to LabThings 1.1.3 ([738f527](https://gitlab.com/openflexure/openflexure-microscope-server/commit/738f527))
- Warn about unused settings when updating ([1e273be](https://gitlab.com/openflexure/openflexure-microscope-server/commit/1e273be))

# [2.8.0-beta.2](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.8.0-beta.1...v2.8.0-beta.2) (2020-11-11)

- 2.8.0-beta.2 ([8c180ca](https://gitlab.com/openflexure/openflexure-microscope-server/commit/8c180ca))
- Added submit button to API origin override form ([1e8978d](https://gitlab.com/openflexure/openflexure-microscope-server/commit/1e8978d))
- Fixed tab switcher background height ([3387c27](https://gitlab.com/openflexure/openflexure-microscope-server/commit/3387c27))
- Scroll to top when pagination switches ([fa909db](https://gitlab.com/openflexure/openflexure-microscope-server/commit/fa909db))

# [2.8.0-beta.1](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.8.0-beta.0...v2.8.0-beta.1) (2020-11-09)

- 2.8.0-beta.1 ([64aa565](https://gitlab.com/openflexure/openflexure-microscope-server/commit/64aa565))
- Added capture count warning ([9e887c0](https://gitlab.com/openflexure/openflexure-microscope-server/commit/9e887c0))
- Added classes for error sources ([7043721](https://gitlab.com/openflexure/openflexure-microscope-server/commit/7043721))
- Added internet, RAM, and storage checkers ([5afff59](https://gitlab.com/openflexure/openflexure-microscope-server/commit/5afff59))
- Added newline symbols ([121afb4](https://gitlab.com/openflexure/openflexure-microscope-server/commit/121afb4))
- Added picamera import checker ([013aebd](https://gitlab.com/openflexure/openflexure-microscope-server/commit/013aebd))
- Added platform info to output ([8349b37](https://gitlab.com/openflexure/openflexure-microscope-server/commit/8349b37))
- Added stage tests and tidied up rescue ([0989a50](https://gitlab.com/openflexure/openflexure-microscope-server/commit/0989a50))
- Allow running tasks to be resumed ([ac382b7](https://gitlab.com/openflexure/openflexure-microscope-server/commit/ac382b7))
- Changed default pollInterval to 1s ([5a32e8a](https://gitlab.com/openflexure/openflexure-microscope-server/commit/5a32e8a))
- Changed newline symbols ([061618d](https://gitlab.com/openflexure/openflexure-microscope-server/commit/061618d))
- Changed reload to 60s timeout ([b98522a](https://gitlab.com/openflexure/openflexure-microscope-server/commit/b98522a))
- Fixed duplicated polling timers ([fcd9fa4](https://gitlab.com/openflexure/openflexure-microscope-server/commit/fcd9fa4))
- Fixed GPU preview requests happening regardless of settings ([5feba6a](https://gitlab.com/openflexure/openflexure-microscope-server/commit/5feba6a))
- Fixed ignored kwargs ([eae3124](https://gitlab.com/openflexure/openflexure-microscope-server/commit/eae3124))
- Formatting and linting ([e416563](https://gitlab.com/openflexure/openflexure-microscope-server/commit/e416563))
- Formatting and linting ([cd61ad3](https://gitlab.com/openflexure/openflexure-microscope-server/commit/cd61ad3))
- Reduced long-term lock usage ([4c61f0c](https://gitlab.com/openflexure/openflexure-microscope-server/commit/4c61f0c))
- Removed in-memory full metadata from CaptureObject ([9dab242](https://gitlab.com/openflexure/openflexure-microscope-server/commit/9dab242))
- Removed port scanning warning ([5319beb](https://gitlab.com/openflexure/openflexure-microscope-server/commit/5319beb))
- Removed unused import ([cbea2f5](https://gitlab.com/openflexure/openflexure-microscope-server/commit/cbea2f5))
- Removed unused output ([471ee5e](https://gitlab.com/openflexure/openflexure-microscope-server/commit/471ee5e))
- Style and linting ([de135ab](https://gitlab.com/openflexure/openflexure-microscope-server/commit/de135ab))
- Support tuple-split strings as error messages ([f12671f](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f12671f))
- Tidied up logging ([06fb81e](https://gitlab.com/openflexure/openflexure-microscope-server/commit/06fb81e))
- Upped capture count warning to 10000 ([0647acd](https://gitlab.com/openflexure/openflexure-microscope-server/commit/0647acd))

# [2.8.0-beta.0](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.7.0...v2.8.0-beta.0) (2020-11-03)

- 2.8.0-beta.0 ([368f86c](https://gitlab.com/openflexure/openflexure-microscope-server/commit/368f86c))
- Fixed reloading timestamp in new format ([86732b8](https://gitlab.com/openflexure/openflexure-microscope-server/commit/86732b8))
- Formatting ([32cf2de](https://gitlab.com/openflexure/openflexure-microscope-server/commit/32cf2de))
- Removed full capture metadata from top-level resource list ([b69c903](https://gitlab.com/openflexure/openflexure-microscope-server/commit/b69c903))
- Simplified build_captures_from_exif ([6c51da5](https://gitlab.com/openflexure/openflexure-microscope-server/commit/6c51da5))
- Updated client to simplify capture data ([982154c](https://gitlab.com/openflexure/openflexure-microscope-server/commit/982154c))

# [2.7.0](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.6.5...v2.7.0) (2020-10-30)

- 2.7.0 ([c58827a](https://gitlab.com/openflexure/openflexure-microscope-server/commit/c58827a))
- Add header to put request to ensure string is sent ([97ff8d4](https://gitlab.com/openflexure/openflexure-microscope-server/commit/97ff8d4))
- Added API LogFileView ([f1d0ea5](https://gitlab.com/openflexure/openflexure-microscope-server/commit/f1d0ea5))
- Added download attribute to log file downloader ([3ba9718](https://gitlab.com/openflexure/openflexure-microscope-server/commit/3ba9718))
- Added extra divider ([46120ea](https://gitlab.com/openflexure/openflexure-microscope-server/commit/46120ea))
- Added logger tab ([21b150b](https://gitlab.com/openflexure/openflexure-microscope-server/commit/21b150b))
- Added pagination to gallery ([e1cca30](https://gitlab.com/openflexure/openflexure-microscope-server/commit/e1cca30))
- Always use builtin JPEG thumbnails ([10d264c](https://gitlab.com/openflexure/openflexure-microscope-server/commit/10d264c))
- Code formatting ([ee8a2fc](https://gitlab.com/openflexure/openflexure-microscope-server/commit/ee8a2fc))
- Code formatting ([7f53ff2](https://gitlab.com/openflexure/openflexure-microscope-server/commit/7f53ff2))
- Code formatting ([76094b2](https://gitlab.com/openflexure/openflexure-microscope-server/commit/76094b2))
- Code formatting and linting ([250fd07](https://gitlab.com/openflexure/openflexure-microscope-server/commit/250fd07))
- Explicit JPEG thumbnail on capture ([09849ce](https://gitlab.com/openflexure/openflexure-microscope-server/commit/09849ce))
- Fixed capture link generator when passed a dict ([353450d](https://gitlab.com/openflexure/openflexure-microscope-server/commit/353450d))
- Fixed gallery overflow ([e3a45b8](https://gitlab.com/openflexure/openflexure-microscope-server/commit/e3a45b8))
- Fixed incorrect import ([89b1f9d](https://gitlab.com/openflexure/openflexure-microscope-server/commit/89b1f9d))
- Fixed node_modules exclusion from Black ([13b4c84](https://gitlab.com/openflexure/openflexure-microscope-server/commit/13b4c84))
- Fixed tab bar scrolling ([279cc03](https://gitlab.com/openflexure/openflexure-microscope-server/commit/279cc03))
- Fixed thumbnail data fallback ([32ec2b4](https://gitlab.com/openflexure/openflexure-microscope-server/commit/32ec2b4))
- Fixed thumbnail generation for video_port captures ([1f12154](https://gitlab.com/openflexure/openflexure-microscope-server/commit/1f12154))
- Fixed View import ([37c5e20](https://gitlab.com/openflexure/openflexure-microscope-server/commit/37c5e20))
- Only save configuration if successfully load new stage type ([b111a4e](https://gitlab.com/openflexure/openflexure-microscope-server/commit/b111a4e))
- Properly set up ESLint ([81ca64e](https://gitlab.com/openflexure/openflexure-microscope-server/commit/81ca64e))
- Rearranged main components ([eff03bf](https://gitlab.com/openflexure/openflexure-microscope-server/commit/eff03bf))
- Removed pointless GPU preview log ([3cfbb40](https://gitlab.com/openflexure/openflexure-microscope-server/commit/3cfbb40))
- Separated move_and_measure locks ([da490fd](https://gitlab.com/openflexure/openflexure-microscope-server/commit/da490fd))
- Upgraded to LabThings 1.1.2 ([654f722](https://gitlab.com/openflexure/openflexure-microscope-server/commit/654f722))
- Use `application/json` for put ([2f08bd3](https://gitlab.com/openflexure/openflexure-microscope-server/commit/2f08bd3))

## [2.6.5](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.6.4...v2.6.5) (2020-10-26)

- 2.6.5 ([9ff0aa3](https://gitlab.com/openflexure/openflexure-microscope-server/commit/9ff0aa3))
- Close #186 ([729e473](https://gitlab.com/openflexure/openflexure-microscope-server/commit/729e473)), closes [#186](https://gitlab.com/openflexure/openflexure-microscope-server/issues/186)

## [2.6.4](https://gitlab.com/openflexure/openflexure-microscope-server/compare/v2.6.3...v2.6.4) (2020-10-26)

- 2.6.4 ([7a12dfb](https://gitlab.com/openflexure/openflexure-microscope-server/commit/7a12dfb))
- Added a settle time to all stage moves ([58f7686](https://gitlab.com/openflexure/openflexure-microscope-server/commit/58f7686))
- Added a spiral scan option ([ad8fd54](https://gitlab.com/openflexure/openflexure-microscope-server/commit/ad8fd54))
- Changed frame tracker logging level to debug ([9b4031f](https://gitlab.com/openflexure/openflexure-microscope-server/commit/9b4031f))
- Close #148 ([a258b63](https://gitlab.com/openflexure/openflexure-microscope-server/commit/a258b63)), closes [#148](https://gitlab.com/openflexure/openflexure-microscope-server/issues/148)
- Code formatting ([601e51d](https://gitlab.com/openflexure/openflexure-microscope-server/commit/601e51d))
- Fast AF use normal stream ([574f4a6](https://gitlab.com/openflexure/openflexure-microscope-server/commit/574f4a6))
- Reset tracker frames after each mode ([459a8dc](https://gitlab.com/openflexure/openflexure-microscope-server/commit/459a8dc))
