"""Functionality for planning scan routes.

A scan route can be planned by a ScanPlanner. There is a base class ``ScanPlanner``,
and then a child class that is still generic called RectGridPlanner that helps planning
anything where the movements are on a regtangular grid. RectGridPlanner has two usable
child classes:

* SmartSpiral - For spiralling around a samples but adjusting when background is
    detected
* RegularGridPlanner - For Raster and Snake scanning.
"""

# Future annotations needed for typhinting same class in __eq__ method. Other option
# would be to import Union and use a string.
from __future__ import annotations

import enum
import logging
from copy import copy
from typing import Any, Literal, Optional, TypeAlias

import numpy as np

LOGGER = logging.getLogger(__name__)

XYPos: TypeAlias = tuple[int, int]
XYZPos: TypeAlias = tuple[int, int, int]
XYPosList: TypeAlias = list[XYPos]
XYZPosList: TypeAlias = list[XYZPos]


class DistanceMetric(enum.Enum):
    """An enum for selecting distance metrics for grids.

    Grid distance metrics are:

    * Chebyshev (``CHEBYSHEV``) which is the larger of the number of x or y moves
        in the grid.
    * Manhattan (``MANHATTAN``) which is the number of moves between the two points
        following the grid. Or
    * Euclidean (``EUCLIDEAN``) which is the length of the direct route.
    """

    CHEBYSHEV = enum.auto()
    MANHATTAN = enum.auto()
    EUCLIDEAN = enum.auto()


def enforce_xy_tuple(value: XYPos) -> XYPos:
    """Check input is a tuple and is of length 2.

    If possible it will coerce the value to a tuple.

    :raises ValueError: if the input cannot be coerced to a tuple of length 2.
    """
    if not isinstance(value, (list, tuple)):
        raise TypeError("2 value tuple expected")
    if not len(value) == 2:
        raise ValueError("2 value tuple expected")
    if isinstance(value, list):
        return tuple(value)
    return value


def enforce_xyz_tuple(value: XYZPos) -> XYZPos:
    """Check input is a tuple and is of length 3.

    If possible it will coerce the value to a tuple.

    :raises ValueError: if the input cannot be coerced to a tuple of length 3.
    """
    if not isinstance(value, (list, tuple)):
        raise TypeError("3 value tuple expected")
    if not len(value) == 3:
        raise ValueError("3 value tuple expected")
    if isinstance(value, list):
        return tuple(value)
    return value


# Disable PLW1641, this warns against hash not being set when equals is. But the
# class shouldn't be hashable.
class FutureScanLocation:  # noqa PLW1641
    """Data for information on future locations to scan.

    This object is only used for internal calculation and data storage it shouldn't
    be an input or output of public methods.
    """

    def __init__(self, xy_pos: XYPos, **kwargs: Any) -> None:
        """Initialise FutureScanLocation with an xy position.

        :param xy_pos: The (x, y) position to scan.
        :param kwargs: Any other information about the location. This will be passed
            through to the VisitedScanLocation object.
        """
        self._xy_pos = enforce_xy_tuple(xy_pos)
        self.planner_data = copy(kwargs)

    @property
    def xy_tuple(self) -> XYPos:
        """The xy position tuple."""
        return self._xy_pos

    def __eq__(self, other: Any) -> bool:
        """Check for equality, only checks the xy position.

        Will check against tuple or other FutureScanLocation object.
        """
        if isinstance(other, FutureScanLocation):
            return self._xy_pos == other.xy_tuple
        if isinstance(other, tuple) and len(other) == 2:
            return self._xy_pos[:2] == other
        return NotImplemented


# Disable PLW1641, this warns against hash not being set when equals is. But the
# class shouldn't be hashable.
class VisitedScanLocation:  # noqa PLW1641
    """Data for information on locations already visited during a scan.

    This object is only used for internal calculation and data storage it shouldn't
    be an input or output of public methods.
    """

    def __init__(
        self, xyz_pos: XYZPos, imaged: bool, focused: bool, **kwargs: Any
    ) -> None:
        """Initialise VisitedScanLocation with an xyz-position, and whether imaged/focused.

        :param xyz_pos: The (x, y, z) position visited.
        :param imaged: True if an image was taken, False if not (due to background
            detect)
        :param focused: True if autofocus completed successfully
        :param kwargs: Any other information about the location. This should be passed
            through to from the FutureScanLocation object that requested the location
            to be scanned.
        """
        self._xyz_pos = enforce_xyz_tuple(xyz_pos)
        self.imaged = imaged
        self.focused = focused
        self.planner_data = copy(kwargs)

    @property
    def xyz_tuple(self) -> XYZPos:
        """The xyz position tuple."""
        return self._xyz_pos

    @property
    def xy_tuple(self) -> XYPos:
        """The xy position tuple."""
        return self._xyz_pos[:2]

    def __eq__(self, other: Any) -> bool:
        """Check for equality, only checks the xyz-position or xy-position if z isn't available.

        Will check xyz-position against 3-value tuples and other VisitedScanLocation
        objects.

        Will check xy-position against 2-value tuples and FutureScanLocation objects.
        """
        if isinstance(other, VisitedScanLocation):
            return self._xyz_pos == other.xyz_tuple
        if isinstance(other, FutureScanLocation):
            return self._xyz_pos[:2] == other.xy_tuple
        if isinstance(other, tuple):
            if len(other) == 2:
                return self._xyz_pos[:2] == other
            if len(other) == 3:
                return self._xyz_pos == other
        return NotImplemented


class ScanPlanner:
    """A base class for a scan planner.

    This should never be used directly for a scan, it should be subclassed.

    Each subclass should implement at least the methods with NotImplementedError
    set:

    * ``_parse()`` - to parse the planner_settings dictionary, saving values to class
        variables
    * ``_initial_location_list()`` - Sets the list of locations for the scan to follow

    For a simple scan pattern this should be sufficient. For more complex ones that
    dynamically adjust the path it is suggested to override ``mark_location_visited()``
    calling ``super().mark_location_visited()`` at the start of the method so that all
    locations are adjusted.

    When subclassing be sure to use ``enforce_xy_tuple`` and ``enforce_xyz_tuple`` on
    any user data before running.
    """

    def __init__(
        self, initial_position: XYPos, planner_settings: Optional[dict] = None
    ) -> None:
        """Set up lists for the path planning, and scan history."""
        self._initial_position = enforce_xy_tuple(initial_position)
        self._parse(planner_settings)

        self._remaining_locations: list[FutureScanLocation] = (
            self._initial_location_list()
        )
        self._path_history: list[VisitedScanLocation] = []

    @property
    def scan_complete(self) -> bool:
        """Return True if there are no locations left to scan."""
        return not self._remaining_locations

    @property
    def completion_reason(self) -> str:
        """A human readable explanation of why the scan has (or hasn't) finished.

        The base implementation only distinguishes "still going" from "ran out of
        planned locations". Subclasses that can stop for more than one reason (for
        example, hitting a range limit vs. running out of sample to follow) should
        override this to give a more specific explanation, as this is used for
        end-of-scan logging.
        """
        if not self.scan_complete:
            raise RuntimeError(
                "Attempted to check scan completion reason before scan completed."
            )
        # If the scan planner knows the scan is complete then all sites are visited.
        return "Scan completed: every planned location was visited."

    @property
    def remaining_locations(self) -> XYPosList:
        """Property to access a copy of the remaining_locations."""
        return [loc.xy_tuple for loc in self._remaining_locations]

    @property
    def imaged_locations(self) -> XYZPosList:
        """Property to access a copy of the imaged_locations."""
        return [loc.xyz_tuple for loc in self._path_history if loc.imaged]

    @property
    def focused_locations(self) -> list[VisitedScanLocation]:
        """Property to access a copy of the focused_locations."""
        return [loc for loc in self._path_history if loc.focused]

    @property
    def focused_locations_xyz(self) -> XYZPosList:
        """Property to access a copy of the focused_locations."""
        return [loc.xyz_tuple for loc in self._path_history if loc.focused]

    @property
    def path_history(self) -> XYPosList:
        """Property to access a copy of the path_history."""
        return [loc.xy_tuple for loc in self._path_history]

    def _parse(self, planner_settings: Optional[dict] = None) -> None:
        """Parse any settings sent to this planner and store them if needed."""
        raise NotImplementedError("Did you call the ScanPlanner base class?")

    def _initial_location_list(self) -> list[FutureScanLocation]:
        """Set the initial list of locations for this scan planner.

        This is called on initialisation.

        For a simple grid scan/snake scan this would be all locations to move to.

        :return: A list of FutureScanLocation objects with all planned locations.
        """
        raise NotImplementedError("Did you call the ScanPlanner base class?")

    def position_visited(self, position: XYPos | FutureScanLocation) -> bool:
        """Return True if input scan position has been visited before."""
        # Ensure tuple for correct matching!
        return position in self._path_history

    def get_visited_location(
        self, position: XYPos | XYZPos | FutureScanLocation
    ) -> VisitedScanLocation:
        """Return the scan location from the history that matches the input position."""
        # Ignoring type as self._path_history has type List[VisitedScanLocation], and
        # VisitedScanLocation implements __eq__ for XYPos & XYZPos & FutureScanLocation
        # however this is not statically detectable by MyPy
        index = self._path_history.index(position)  # type: ignore[arg-type]
        return self._path_history[index]

    def position_planned(self, position: XYPos | FutureScanLocation) -> bool:
        """Return True if input scan position position is planned."""
        # Ensure tuple for correct matching!
        return position in self._remaining_locations

    def get_next_location_and_z_estimate(self) -> tuple[XYPos, Optional[int]]:
        """Return the next location to scan and its estimated z-position.

        Note z-position may be None! This indicates that the current z, position
        should be used.
        """
        if self.scan_complete:
            raise RuntimeError("Can't get next position, scan is complete")

        next_location = self._remaining_locations[0].xy_tuple

        # Each scanner defines its own method of choosing a representative nearby site
        closest_pos = self.select_nearby_focus_site(next_location)
        z = None if closest_pos is None else closest_pos[2]

        return next_location, z

    def select_nearby_focus_site(self, next_location: XYPos) -> Optional[XYZPos]:
        """Return the focused site near xy_pos according to the tiebreak."""
        raise NotImplementedError("Did you call the ScanPlanner base class?")

    def mark_location_visited(
        self, xyz_pos: XYZPos, imaged: bool, focused: bool
    ) -> None:
        """Mark the location as visited.

        :param xyz_pos: the x_y_z position
        :param imaged: true if an image was taken, false if not (due to background detect)
        :param focused: true if autofocus completed successfully
        """
        # ensure is tuple!
        xyz_pos = enforce_xyz_tuple(xyz_pos)

        # Remove the expected position from the remaining locations list
        # and check it's correct
        expected_pos = self._remaining_locations.pop(0)
        if xyz_pos[:2] != expected_pos.xy_tuple:
            raise RuntimeError("Wrong scan location visited!")

        # Append xy position for path_history
        self._path_history.append(
            VisitedScanLocation(
                xyz_pos=xyz_pos,
                imaged=imaged,
                focused=focused,
                **expected_pos.planner_data,
            )
        )

    def _grid_to_future_locations(
        self,
        grid: list[list[XYPos]],
    ) -> list[FutureScanLocation]:
        """Flatten a 2D grid of coordinates into flat list of FutureScanLocation objects.

        :param grid: A 2D nested list of XY coordinates

        :return: A flattened list of FutureScanLocations
        """
        # Loop over each location in each line to flatten grid into single list.
        return [FutureScanLocation(location) for line in grid for location in line]


class RectGridPlanner(ScanPlanner):
    """Base class for planners that operate on a rectangular grid."""

    _dx: int = 0
    _dy: int = 0

    def _parse(self, planner_settings: Optional[dict] = None) -> None:
        expected_keys = ["dx", "dy"]
        invalid_msg = "RectGridPlanner requires planner_settings with keys: "
        if not planner_settings or not all(
            k in planner_settings for k in expected_keys
        ):
            raise KeyError(invalid_msg + ",".join(expected_keys))

        self._dx = int(planner_settings["dx"])
        self._dy = int(planner_settings["dy"])

    def _adjacent_positions(self, xy_pos: XYPos) -> XYPosList:
        return [
            (xy_pos[0] - self._dx, xy_pos[1]),
            (xy_pos[0] + self._dx, xy_pos[1]),
            (xy_pos[0], xy_pos[1] - self._dy),
            (xy_pos[0], xy_pos[1] + self._dy),
        ]

    def moves_between(
        self,
        starting_pos: XYPos | np.ndarray | FutureScanLocation | VisitedScanLocation,
        ending_pos: XYPos | np.ndarray | FutureScanLocation | VisitedScanLocation,
        metric: DistanceMetric,
    ) -> float:
        """Return displacement in grid-move units as a numpy array [dx_moves, dy_moves].

        :param starting_pos: the position to measure from
        :param ending_pos: the position to measure to
        :param metric: How the distance is calculated. See `DistanceMetric`
        """
        if isinstance(starting_pos, (FutureScanLocation, VisitedScanLocation)):
            starting_pos = starting_pos.xy_tuple
        if isinstance(ending_pos, (FutureScanLocation, VisitedScanLocation)):
            ending_pos = ending_pos.xy_tuple

        move_size = np.array([self._dx, self._dy], dtype="float64")

        starting_pos = np.array(starting_pos, dtype="float64")
        ending_pos = np.array(ending_pos, dtype="float64")

        displacement = (ending_pos - starting_pos) / move_size
        if metric == DistanceMetric.CHEBYSHEV:
            return float(np.max(np.abs(displacement)))
        if metric == DistanceMetric.MANHATTAN:
            return float(np.sum(np.abs(displacement)))
        return float(np.linalg.norm(displacement))

    def _intermediate_position(self, xy_pos1: XYPos, xy_pos2: XYPos) -> XYPos:
        """Return an (x,y) position halfway between two input positions."""
        x = (xy_pos1[0] + xy_pos2[0]) // 2
        y = (xy_pos1[1] + xy_pos2[1]) // 2
        return (x, y)

    def select_nearby_focus_site(self, next_position: XYPos) -> Optional[XYZPos]:
        """Return a focused site near the given position to estimate Z for the next move.

        Looks for all previously focused locations that are within the scan
        step size (self._dx, self._dy) of ``next_position``. Among these nearby focused
        sites, it returns the most recently imaged one.

        This is suitable for raster or snake scans, where the scan may move along a row
        or column and then jump to a new row/column. If no nearby focused sites exist,
        returns None.

        :param next_position: The XY position where the next image will be taken.
        :return: The XYZ tuple of the closest and most recent focused site, or None if
                no focused locations exist.
        """
        focused_locations = self.focused_locations
        if not focused_locations:
            return None

        def sort_key(pos: VisitedScanLocation) -> float:
            return self.moves_between(next_position, pos, DistanceMetric.MANHATTAN)

        # Sort by the total number of dx and dy moves between sites, then by most
        # recent. Using reverse=True puts the most recent, nearest at the end
        nearby_focus_locations = sorted(focused_locations, key=sort_key, reverse=True)

        # Pick the most recent nearby site
        return nearby_focus_locations[-1].xyz_tuple


class SmartSpiral(RectGridPlanner):
    """A scan planner that spirals outward from the centre, prioritising short moves.

    This planner spirals out from the centre, but prioritises short moves over rigidly
    sticking to minimising radius from the centre of the scan.

    Each time and image is taken the four neighbouring images are added
    to the list of positions to image (unless they are already listed or
    tried). However, if a location is not imaged due no sample being detected,
    then neighbouring positions are not imaged.

    The next image taken is the fewest scan sites (moves in dx and dy) from the current
    site, with ties broken by minimising the moves away from the start of the scan.
    Final tiebreak is the distance to each site, in motor steps rather than multiple of
    dx and dy.
    """

    # The maximum distance for the scan to run in any direction.
    # Any future moves which would move beyond this distance are not appended.
    _max_dist: int = 0

    # Set to True the first time a candidate position is rejected for being
    # beyond _max_dist. Used to distinguish "ran out of range" from "ran out
    # of sample" when reporting why the scan finished.
    _reached_max_dist: bool = False

    # When enabled, probe one half-step between adjacent sample/background fields.
    # This locates tissue boundaries more precisely, at the cost of an extra move and
    # classification for every boundary crossing.
    _refine_background_boundaries: bool = True

    def _is_primary_location(
        self, location: FutureScanLocation | VisitedScanLocation
    ) -> bool:
        """Return True if input is a primary location not a secondary (intermediate) location."""
        return location.planner_data["primary"]

    @property
    def secondary_locations(self) -> XYZPosList:
        """A list of all secondary (intermediate) locations."""
        return [
            loc.xyz_tuple
            for loc in self._path_history
            if not self._is_primary_location(loc)
        ]

    @property
    def completion_reason(self) -> str:
        """Explain why the spiral scan finished.

        A spiral scan stops adding new locations once every remaining edge of
        the spiral has either found background (nothing left to follow) or
        would move beyond ``max_dist``. This distinguishes those two cases:
        if any candidate location was ever rejected for being out of range,
        the range limit is reported as (at least part of) the reason;
        otherwise the scan concluded naturally because it was surrounded by
        background on all sides.
        """
        if not self.scan_complete:
            raise RuntimeError(
                "Attempted to check scan completion reason before scan completed."
            )
        if self._reached_max_dist:
            return (
                "Scan stopped because it reached the maximum allowed range of motion."
            )
        return "Scan completed: the scanned area was fully surrounded by background."

    def _parse(self, planner_settings: Optional[dict] = None) -> None:
        super()._parse(planner_settings)

        if not planner_settings or "max_dist" not in planner_settings:
            raise KeyError("SmartSpiral requires max_dist")

        self._max_dist = int(planner_settings["max_dist"])
        refine_boundaries = planner_settings.get("refine_background_boundaries", True)
        if not isinstance(refine_boundaries, bool):
            raise TypeError("refine_background_boundaries must be a boolean")
        self._refine_background_boundaries = refine_boundaries

    def _initial_location_list(self) -> list[FutureScanLocation]:
        """Set the initial list of locations for this scan planner.

        This is called on initialisation.

        For smart spiral this is just the first point
        """
        return [FutureScanLocation(self._initial_position, primary=True)]

    def mark_location_visited(
        self, xyz_pos: XYZPos, imaged: bool = True, focused: bool = True
    ) -> None:
        """Mark the location as visited.

        :param xyz_pos: the x_y_z position
        :param imaged: true if an image was taken, false if not (due to background detect)
        :param focused: true if autofocus completed successfully
        """
        # First call the base class to update the positions
        super().mark_location_visited(xyz_pos, imaged, focused)

        xy_pos = enforce_xy_tuple(xyz_pos[:2])
        if self._is_primary_location(self._path_history[-1]):
            if imaged:
                self._add_surrounding_positions(xy_pos)
            elif self._refine_background_boundaries:
                self._add_intermediate_positions(xy_pos)
            # Don't re-sort after imaging a secondary location or it can cause scan
            # direction to reverse, breaking the spiral.
            self._re_sort_remaining_locations(xy_pos)

    def _add_surrounding_positions(self, xy_pos: XYPos) -> None:
        """Add the 4 surrounding positions to the list of remaining locations to visit.

        This adds the surrounding positions (with 4 point connectivity) to the
        remaining locations list if they are not:

        * too far away
        * already planned
        * already visited

        If the already visited position was not imaged then an intermediate location is
        added. See also self._add_intermediate_positions() for adding intermediate
        locations after visiting a location that was not imaged.
        """
        new_positions = self._adjacent_positions(xy_pos)

        for new_pos in new_positions:
            # Skip position if already planned
            if self.position_planned(new_pos):
                continue
            if self.position_visited(new_pos):
                # Get the VisitedScanLocation object if already visited
                visited = self.get_visited_location(new_pos)
                if visited.imaged or not self._refine_background_boundaries:
                    # If this adjacent position was imaged successfully already then skip.
                    # When boundary refinement is disabled, a known background neighbour
                    # is also final and must not create a half-step probe.
                    continue
                # If it wasn't imaged add an intermediate location between the
                # last imaged position and this one.
                i_pos = self._intermediate_position(xy_pos, new_pos)
                # Set primary=False surrounding images are not added once imaged.
                i_loc = FutureScanLocation(i_pos, primary=False)
                # Append instantly without checking max_distance as this is between
                # imaged points.
                self._remaining_locations.append(i_loc)
                continue

            dist = distance_between(new_pos, self._initial_position)
            if dist > self._max_dist:
                LOGGER.debug("Rejected moving to %s as it is out of range", new_pos)
                self._reached_max_dist = True
                continue
            new_loc = FutureScanLocation(new_pos, primary=True)
            self._remaining_locations.append(new_loc)

    def _add_intermediate_positions(self, xy_pos: XYPos) -> None:
        """Add intermediate points after locating a background location.

        This is called after an image is recorded that was background. Intermediate
        locations are added between any adjacent locations that were successfully
        imaged due to being labelled as containing sample.

        Note that in the case that an imaged location has an adjacent background image
        then adding the intermediate image will be handled by
        _add_surrounding_positions().
        """
        surrounding_positions = self._adjacent_positions(xy_pos)

        for surr_pos in surrounding_positions:
            if self.position_visited(surr_pos):
                # Get the VisitedScanLocation object if already visited
                visited = self.get_visited_location(surr_pos)
                if not visited.imaged:
                    # If it wasn't imaged then skip this position
                    continue
                # If surrounding location was imaged add an intermediate location
                # between the most recent position and this imaged position.
                i_pos = self._intermediate_position(xy_pos, surr_pos)
                # Set primary=False surrounding images are not added once imaged.
                i_loc = FutureScanLocation(i_pos, primary=False)
                # Append instantly without checking max_distance as this is between
                # imaged points.
                self._remaining_locations.append(i_loc)

    def _re_sort_remaining_locations(self, current_pos: XYPos) -> None:
        """Sort the remaining positions based on the current location."""

        # Defined rather than use a lambda for readability
        def sort_key(pos: FutureScanLocation) -> tuple[bool, float, float, float]:
            return (
                self._is_primary_location(pos),  # False sorts low
                self.moves_between(current_pos, pos, DistanceMetric.CHEBYSHEV),
                self.moves_between(
                    self._initial_position, pos, DistanceMetric.CHEBYSHEV
                ),
                distance_between(current_pos, pos),
            )

        self._remaining_locations.sort(key=sort_key)

    def select_nearby_focus_site(self, xy_pos: XYPos) -> Optional[XYZPos]:
        """Return the xyz position of the nearby site with the lowest z position.

        Lowest position is best, as starting too high causes smart stacking to
        autofocus and restart. Starting too low just requires extra movements in +z.
        Nearby is defined as within 1.1 times the larger of the x and y scan offsets.

        If no focused sites are within this range, use the height of the nearest
        focused site.

        Returns None if no focused locations are present
        """
        # save to variable rather than search for focussed sites each time.
        focused_locations = self.focused_locations_xyz
        if not focused_locations:
            return None

        # must be float64 to deal with large coordinates
        current_pos = np.array(xy_pos, dtype="float64")
        focused_arr = np.array(focused_locations, dtype="float64")

        # Find focused sites within dx and dy
        dx_ok = np.abs(focused_arr[:, 0] - current_pos[0]) <= self._dx
        dy_ok = np.abs(focused_arr[:, 1] - current_pos[1]) <= self._dy
        nearby_indices = np.where(dx_ok & dy_ok)[0]

        # If no neighbouring sites were focused, choose the closest
        if len(nearby_indices) == 0:
            deltas = focused_arr[:, :2] - current_pos
            dists = np.linalg.norm(deltas, axis=1)
            min_dist = np.min(dists)
            nearby_indices = np.where(dists == min_dist)[0]

        nearby_sites = focused_arr[nearby_indices]

        # Choose the lowest z
        min_z = np.min(nearby_sites[:, 2])

        # Among those with min z, choose the most recent
        chosen_site = nearby_sites[nearby_sites[:, 2] == min_z][-1]

        return (
            int(chosen_site[0]),
            int(chosen_site[1]),
            int(chosen_site[2]),
        )


class RegularGridPlanner(RectGridPlanner):
    """A scan planner that performs a snake or a raster scan.

    Direction cannot yet be set it always scans, right and down from a corner.

    This planner starts at the corner of the region to scan, snaking back and forth,
    starting moving right and down (assuming positive dx and dy.)
    """

    _x_count: int = 0
    _y_count: int = 0
    _style: Literal["snake", "raster"]

    def _parse(self, planner_settings: Optional[dict] = None) -> None:
        super()._parse(planner_settings)

        expected_keys = ["x_count", "y_count", "style"]
        invalid_msg = "RegularGrid requires planner_settings with keys: "
        if not planner_settings or not all(
            k in planner_settings for k in expected_keys
        ):
            raise KeyError(invalid_msg + ",".join(expected_keys))

        self._x_count = int(planner_settings["x_count"])
        self._y_count = int(planner_settings["y_count"])
        style = planner_settings["style"]
        if style not in ("snake", "raster"):
            raise ValueError(
                f"Unknown regular grid style {style}. Use snake or raster."
            )
        self._style = style

    def _initial_location_list(self) -> list[FutureScanLocation]:
        """Set the initial list of locations for this scan planner.

        This is called on initialisation.

        For snake scan, this is the full grid, and none will be added during scanning.
        """
        grid = create_rectangular_scan_path(
            starting_pos=self._initial_position,
            x_count=self._x_count,
            y_count=self._y_count,
            dx=self._dx,
            dy=self._dy,
            style=self._style,
        )

        return self._grid_to_future_locations(grid)


class FrozenGridPlanner(RectGridPlanner):
    """Adapt a validated external route to the existing scan-runner interface.

    This class does not choose an order.  It retains the immutable sequence returned
    by the separately installed planner while reusing the existing nearby-focus lookup.
    """

    _locations: tuple[XYPos, ...] = ()

    def _parse(self, planner_settings: Optional[dict] = None) -> None:
        super()._parse(planner_settings)
        if not planner_settings or "locations" not in planner_settings:
            raise KeyError("FrozenGridPlanner requires locations")
        values = planner_settings["locations"]
        if not isinstance(values, (list, tuple)) or not values:
            raise ValueError("FrozenGridPlanner locations must be a non-empty sequence")
        locations = tuple(enforce_xy_tuple(value) for value in values)
        if locations[0] != self._initial_position:
            raise ValueError("Frozen route must start at the scan initial position")
        if len(locations) != len(set(locations)):
            raise ValueError("Frozen route contains duplicate locations")
        self._locations = locations

    def _initial_location_list(self) -> list[FutureScanLocation]:
        return [FutureScanLocation(location) for location in self._locations]


def distance_between(
    current_pos: XYPos | np.ndarray | FutureScanLocation,
    next_pos: XYPos | np.ndarray | FutureScanLocation,
) -> float:
    """Calculate the distance between the two xy positions.

    This was previously called ``distance_to_site``
    """
    if isinstance(current_pos, FutureScanLocation):
        current_pos = current_pos.xy_tuple
    if isinstance(next_pos, FutureScanLocation):
        next_pos = next_pos.xy_tuple
    next_pos = np.array(next_pos, dtype="float64")
    current_pos = np.array(current_pos, dtype="float64")
    return float(np.linalg.norm(next_pos - current_pos))


def create_rectangular_scan_path(
    *,
    starting_pos: XYPos,
    x_count: int,
    y_count: int,
    dx: int,
    dy: int,
    style: Literal["snake", "raster"],
) -> list[list[XYPos]]:
    """Generate a 2D grid of (x, y) coordinates representing a rectangular scan path.

    The grid is generated from starting_pos, and expanded in the
    positive x and y directions using the provided step sizes. The scan order
    can be either raster (left-to-right for every row) or snake (alternating
    left-to-right and right-to-left per row).

    :param starting_pos: Starting (x, y) position for the scan grid.
    :param x_count: Number of points in the x-direction (columns).
    :param y_count: Number of points in the y-direction (rows).
    :param dx: Step size between points in the x-direction.
    :param dy: Step size between points in the y-direction.
    :param style: Scan pattern style. Either raster or snake.
    :return: Nested list of (x, y) coordinates arranged by row.
    """
    coords: list[list[XYPos]] = []

    # Populate grid with coordinates in a regular grid
    for y_index in range(y_count):  # rows
        row = [
            (starting_pos[0] + x_index * dx, starting_pos[1] + y_index * dy)
            for x_index in range(x_count)
        ]
        if style == "snake" and y_index % 2 == 1:
            row.reverse()
        coords.append(row)
    return coords
