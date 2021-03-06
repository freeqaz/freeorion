"""
This module deals with the autonomous shipdesign from the AI. The design process is class-based.
The basic functionality is defined in the class ShipDesigner. The more specialised classes mostly 
implement the rating function and some additional information for the optimizing algorithms to improve performance. 

Example usage of this module:
import ShipDesignAI
myDesign = ShipDesignAI.MilitaryShipDesigner()
myDesign.additional_specifications.enemy_mine_dmg = 10
best_military_designs = myDesign.optimize_design()  # best designs per planet: (rating,planetID,design_id,cost) tuples


Available ship classes:
- MilitaryShipDesigner: basic military ship
- OrbitalTroopShipDesigner: Troop ships for invasion in the same system
- StandardTroopShipDesigner: Troop ships for invasion of other systems
- OrbitalColonisationShipDesigner: Ships for colonization in the same system
- StandardColonisationShipDesigner: Ships for colonization of other systems
- OrbitalOutpostShipDesigner: Ships for outposting in the same system
- StandardOutpostShipDesigner: Ships for outposting in other systems
- OrbitalDefenseShipDesigner: Ships for stationary defense

Internal use only:
Classes:
- ShipDesignCache: caches information used in this module. Only use the defined instance (variable name "Cache")
- ShipDesigner: base class for all designs. Provides basic and general functionalities
- ColonisationShipDesignerBaseClass: base class for all (specialised) colonisation ships which provides common functionalities
- OutpostShipDesignerBaseClass: same but for outposter ships
- TroopShipDesignerBaseClass: same but for troop ships
- AdditionalSpecifications:  Defines all requirements we have for our designs such as minimum fuel or minimum speed.

global variables:
- Cache: Instance of the ShipDesignCache class - all cached information is stored in here.
"""

# TODO: add hull.detection to interface, then add scout class

import freeOrionAIInterface as fo
import FreeOrionAI as foAI
import ColonisationAI
import copy
import traceback
import math
from collections import Counter
from collections import defaultdict
from freeorion_tools import print_error, UserString

# Define meta classes for the ship parts
ARMOUR = frozenset({fo.shipPartClass.armour})
SHIELDS = frozenset({fo.shipPartClass.shields})
DETECTION = frozenset({fo.shipPartClass.detection})
STEALTH = frozenset({fo.shipPartClass.stealth})
FUEL = frozenset({fo.shipPartClass.fuel})
COLONISATION = frozenset({fo.shipPartClass.colony})
ENGINES = frozenset({fo.shipPartClass.speed})
TROOPS = frozenset({fo.shipPartClass.troops})
WEAPONS = frozenset({fo.shipPartClass.shortRange, fo.shipPartClass.missiles,
                     fo.shipPartClass.fighters, fo.shipPartClass.pointDefense})
ALL_META_CLASSES = frozenset({WEAPONS, ARMOUR, DETECTION, FUEL, STEALTH, SHIELDS, COLONISATION, ENGINES, TROOPS})

# Prefixes for the test ship designs
TESTDESIGN_NAME_BASE = "AI_TESTDESIGN"
TESTDESIGN_NAME_HULL = TESTDESIGN_NAME_BASE+"_HULL"
TESTDESIGN_NAME_PART = TESTDESIGN_NAME_BASE+"_PART"

# Hardcoded preferred hullname for testdesigns - should be a hull without conditions but with maximum different slottypes
TESTDESIGN_PREFERRED_HULL = "SH_BASIC_MEDIUM"

MISSING_REQUIREMENT_MULTIPLIER = -1000
INVALID_DESIGN_RATING = -999  # this needs to be negative but greater than MISSING_REQUIREMENT_MULTIPLIER


class ShipDesignCache(object):
    """This class handles the caching of information used to assess and build shipdesigns in this module.

    Important methods:
    update_for_new_turn(self): Updates the cache for the current turn, to be called once at the beginning of each turn.

    Important members:
    testhulls:                 # set of all hullnames used for testdesigns
    design_id_by_name          # {"designname": designid}
    part_by_partname           # {"partname": part object}
    map_reference_design_name  # {"reference_designname": "ingame_designname"}, cf. _build_reference_name()
    strictly_worse_parts       # strictly worse parts: {"part": ["worsePart1","worsePart2"]}
    hulls_for_planets          # buildable hulls per planet {planetID: ["buildableHull1","buildableHull2",...]}
    parts_for_planets          # buildable parts per planet and slot: {planetID: {slottype1: ["part1","part2"]}}
    best_designs               # {shipclass:{reqTup:{species:{available_parts:{hull:(rating,best_parts)}}}}}
    production_cost            # {planetID: {"partname1": local_production_cost, "hullname1": local_production_cost}}
    production_time            # {planetID: {"partname1": local_production_time, "hullname1": local_production_time}}

    Debug methods:
    print_CACHENAME(self), e.g. print_testhulls: prints content of the cache in some nicer format
    print_all(self): calls all the printing functions
    """

    def __init__(self):
        """Cache is empty on creation"""
        self.testhulls = set()
        self.design_id_by_name = {}
        self.part_by_partname = {}
        self.map_reference_design_name = {}
        self.strictly_worse_parts = {}
        self.hulls_for_planets = {}
        self.parts_for_planets = {}
        self.best_designs = {}
        self.production_cost = {}
        self.production_time = {}

    def update_for_new_turn(self):
        """ Update the cache for the current turn.

        Make sure this function is called once at the beginning of the turn,
        i.e. before any other function of this module is used.
        """
        print
        print "-----   Updating ShipDesign cache for new turn   -----"
        if not self.map_reference_design_name:
            self._build_cache_after_load()
        self._check_cache_for_consistency()
        self._update_cost_cache()
        self._update_buildable_items_this_turn(verbose=False)

    def print_testhulls(self):
        """Print the testhulls cache."""
        print "Testhull cache:", self.testhulls

    def print_design_id_by_name(self):
        """Print the design_id_by_name cache."""
        print "DesignID cache:", self.design_id_by_name

    def print_part_by_partname(self):
        """Print the part_by_partname cache."""
        print "Parts cached by name:", self.part_by_partname

    def print_strictly_worse_parts(self):
        """Print the strictly_worse_parts cache."""
        print "List of strictly worse parts (ignoring slots):"
        for part in self.strictly_worse_parts:
            print "  %s:" % part, self.strictly_worse_parts[part]

    def print_map_reference_design_name(self):
        """Print the ingame, reference name map of shipdesigns."""
        print "Design name map:", self.map_reference_design_name

    def print_hulls_for_planets(self, pid=None):
        """Print the hulls buildable on each planet.

        :param pid: None, int or list of ints
        """
        if pid is None:
            planets = [pid for pid in self.hulls_for_planets]
        elif isinstance(pid, int):
            planets = [pid]
        elif isinstance(pid, list):
            planets = pid
        else:
            print "ERROR: Invalid parameter 'pid' for 'print_hulls_for_planets'. Expected int, list or None."
            return
        print "Hull-cache:"
        get_planet = fo.getUniverse().getPlanet
        for pid in planets:
            print "%s:" % get_planet(pid).name, self.hulls_for_planets[pid]

    def print_parts_for_planets(self, pid=None):
        """Print the parts buildable on each planet.

        :param pid: int or list of ints
        """
        if pid is None:
            planets = [pid for pid in self.parts_for_planets]
        elif isinstance(pid, int):
            planets = [pid]
        elif isinstance(pid, list):
            planets = pid
        else:
            print "FAILURE: Invalid parameter 'pid' for 'print_parts_for_planets'. Expected int, list or None."
            return
        print "Available parts per planet:"
        get_planet = fo.getUniverse().getPlanet
        for pid in planets:
            print "  %s:" % get_planet(pid).name,
            for slot in self.parts_for_planets[pid]:
                print slot, ":", self.parts_for_planets[pid][slot]

    def print_best_designs(self):
        """Print the best designs that were previously found."""
        print "Currently cached best designs:"
        for classname in self.best_designs:
            print classname
            for req_tuple in self.best_designs[classname]:
                print "    ", req_tuple
                for species_tuple in self.best_designs[classname][req_tuple]:
                    print "        ", species_tuple, " # relevant species stats"
                    for avParts in self.best_designs[classname][req_tuple][species_tuple]:
                        print "            ", avParts
                        for hullname in sorted(self.best_designs[classname][req_tuple][species_tuple][avParts].keys(),
                                               reverse=True, key=lambda x: self.best_designs[classname][req_tuple]
                                                                           [species_tuple][avParts][x][0]):
                            print "                ", hullname, ":",
                            print self.best_designs[classname][req_tuple][species_tuple][avParts][hullname]

    def print_production_cost(self):
        """Print production_cost cache."""
        universe = fo.getUniverse()
        print "Cached production cost per planet:"
        for pid in self.production_cost:
            print "  %s:" % universe.getPlanet(pid).name, self.production_cost[pid]

    def print_production_time(self):
        """Print production_time cache."""
        universe = fo.getUniverse()
        print "Cached production cost per planet:"
        for pid in self.production_time:
            print "  %s:" % universe.getPlanet(pid).name, self.production_time[pid]

    def print_all(self):
        """Print the entire ship design cache."""
        print
        print "Printing the ShipDesignAI cache..."
        self.print_testhulls()
        self.print_design_id_by_name()
        self.print_part_by_partname()
        self.print_strictly_worse_parts()
        self.print_map_reference_design_name()
        self.print_hulls_for_planets()
        self.print_parts_for_planets()
        self.print_best_designs()
        self.print_production_cost()
        self.print_production_time()
        print "-----"
        print

    def _update_cost_cache(self):
        """Cache the production cost and time for each part and hull for each planet (with shipyard) for this turn."""
        self.production_cost.clear()
        self.production_time.clear()
        empire = fo.getEmpire()
        empire_id = empire.empireID
        for pid in _get_planets_with_shipyard():
            self.production_time[pid] = {}
            self.production_cost[pid] = {}
            for partname in empire.availableShipParts:
                part = _get_part_type(partname)
                self.production_cost[pid][partname] = part.productionCost(empire_id, pid)
                self.production_time[pid][partname] = part.productionTime(empire_id, pid)
            for hullname in empire.availableShipHulls:
                hull = fo.getHullType(hullname)
                self.production_cost[pid][hullname] = hull.productionCost(empire_id, pid)
                self.production_time[pid][hullname] = hull.productionTime(empire_id, pid)

    def _build_cache_after_load(self):
        """Build cache after loading or starting a game.

        This function is supposed to be called after a reload or at the first turn.
        It reads out all the existing ship designs and then updates the following cache:
        - map_reference_design_name
        - design_id_by_name
        """
        if self.map_reference_design_name or self.design_id_by_name:
            print "WARNING: In ShipDesignAI.py: Cache._build_cache_after_load() called but cache is not empty."
        for design_id in fo.getEmpire().allShipDesigns:
            design = fo.getShipDesign(design_id)
            if TESTDESIGN_NAME_BASE in design.name(False):
                continue
            reference_name = _build_reference_name(design.hull, design.parts)
            self.map_reference_design_name[reference_name] = design.name(False)
            self.design_id_by_name[design.name(False)] = design_id

    def _check_cache_for_consistency(self):
        """Check if the persistent cache is consistent with the gamestate and fix it if not.

        This function should be called once at the beginning of the turn (before update_shipdesign_cache()).
        Especially (only?) in multiplayer games, the shipDesignIDs may sometimes change across turns.
        """
        print "Checking persistent cache for consistency..."
        try:
            for partname in self.part_by_partname:
                cached_name = self.part_by_partname[partname].name
                if cached_name != partname:
                    self.part_by_partname[partname] = fo.getPartType(partname)
                    print "WARNING: Part cache corrupted."
                    print "Expected: %s, got: %s. Cache was repaired." % (partname, cached_name)
        except Exception:
            self.part_by_partname.clear()
            traceback.print_exc()

        corrupted = []
        for designname in self.design_id_by_name:
            try:
                cached_name = fo.getShipDesign(self.design_id_by_name[designname]).name(False)
                if cached_name != designname:
                    print "WARNING: ShipID cache corrupted."
                    print "Expected: %s, got: %s. Repairing cache." % (designname, cached_name)
                    design_id = next(iter([shipDesignID for shipDesignID in fo.getEmpire().allShipDesigns
                                          if designname == fo.getShipDesign(shipDesignID).name(False)]), None)
                    if design_id is not None:
                        self.design_id_by_name[designname] = design_id
                    else:
                        corrupted.append(designname)
            except AttributeError:
                print "WARNING: ShipID cache corrupted. Could not get cached shipdesign. Repairing Cache."
                print traceback.format_exc()  # do not print to stderr as this is an "expected" exception.
                design_id = next(iter([shipDesignID for shipDesignID in fo.getEmpire().allShipDesigns
                                      if designname == fo.getShipDesign(shipDesignID).name(False)]), None)
                if design_id is not None:
                    self.design_id_by_name[designname] = design_id
                else:
                    corrupted.append(designname)
        for corrupted_entry in corrupted:
            del self.design_id_by_name[corrupted_entry]

    def _update_buildable_items_this_turn(self, verbose=False):
        """Calculate which parts and hulls can be built on each planet this turn.

        :param verbose: toggles detailed debugging output.
        :type verbose: bool
        """
        # TODO: Refactor this function
        # The AI currently has no way of checking building requirements of individual parts and hulls directly.
        # It can only check if we can build a design. Therefore, we use specific testdesigns to check if we can
        # build a hull or part.
        # The building requirements are constant so calculate this only once at the beginning of each turn.
        #
        # Code structure:
        #   1. Update hull test designs
        #   2. Get a list of buildable ship hulls for each planet
        #   3. Update ship part test designs
        #   4. Cache the list of buildable ship parts for each planet
        #
        self.hulls_for_planets.clear()
        self.parts_for_planets.clear()
        planets_with_shipyards = _get_planets_with_shipyard()
        if not planets_with_shipyards:
            print "No shipyards found. The design process was aborted."
            return
        get_shipdesign = fo.getShipDesign
        get_hulltype = fo.getHullType
        empire = fo.getEmpire()
        empire_id = empire.empireID
        universe = fo.getUniverse()
        available_hulls = list(empire.availableShipHulls)   # copy so we can sort it locally
        # Later on in the code, we need to find suitable testhulls, i.e. buildable hulls for all slottypes.
        # To reduce the number of lookups, move the hardcoded TESTDESIGN_PREFERED_HULL to the front of the list.
        # This hull should be buildable on each planet and also cover the most common slottypes.
        try:
            idx = available_hulls.index(TESTDESIGN_PREFERRED_HULL)
            available_hulls[0], available_hulls[idx] = available_hulls[idx], available_hulls[0]
        except ValueError:
            print "WARNING: Tried to use '%s' as testhull but it is not in available_hulls." % TESTDESIGN_PREFERRED_HULL,
            print "Please update ShipDesignAI.py according to the new content."
            traceback.print_exc()
        testdesign_names = [get_shipdesign(design_id).name(False) for design_id in empire.allShipDesigns
                            if get_shipdesign(design_id).name(False).startswith(TESTDESIGN_NAME_BASE)]
        testdesign_names_hull = [name for name in testdesign_names if name.startswith(TESTDESIGN_NAME_HULL)]
        testdesign_names_part = [name for name in testdesign_names if name.startswith(TESTDESIGN_NAME_PART)]
        available_slot_types = {slottype for slotlist in [get_hulltype(hull).slots for hull in available_hulls]
                                for slottype in slotlist}
        new_parts = [_get_part_type(part) for part in empire.availableShipParts
                     if part not in self.strictly_worse_parts]
        pid = self.production_cost.keys()[0]  # as only location invariant parts are considered, use arbitrary planet.
        for new_part in new_parts:
            self.strictly_worse_parts[new_part.name] = []
            if not new_part.costTimeLocationInvariant:
                print "new part %s not location invariant!" % new_part.name
                continue
            for part_class in ALL_META_CLASSES:
                if new_part.partClass in part_class:
                    for old_part in [_get_part_type(part) for part in self.strictly_worse_parts
                                     if part != new_part.name]:
                        if not old_part.costTimeLocationInvariant:
                            print "old part %s not location invariant!" % old_part.name
                            continue
                        if old_part.partClass in part_class:
                            if new_part.capacity >= old_part.capacity:
                                a = new_part
                                b = old_part
                            else:
                                a = old_part
                                b = new_part
                            if (self.production_cost[pid][a.name] <= self.production_cost[pid][b.name]
                                    and {x for x in a.mountableSlotTypes} >= {x for x in b.mountableSlotTypes}
                                    and self.production_time[pid][a.name] <= self.production_time[pid][b.name]):
                                self.strictly_worse_parts[a.name].append(b.name)
                                print "Part %s is strictly worse than part %s" % (b.name, a.name)
                    break
        available_parts = sorted(self.strictly_worse_parts.keys(),
                                 key=lambda item: _get_part_type(item).capacity, reverse=True)

        # in case of a load, we need to rebuild our Cache.
        if not self.testhulls:
            print "Testhull cache not found. This may happen only at first turn after game start or load."
            for hullname in available_hulls:
                des = [des for des in testdesign_names_part if des.endswith(hullname)]
                if des:
                    self.testhulls.add(hullname)
            if verbose:
                print "Rebuilt Cache. The following hulls are used in testdesigns for parts: ", self.testhulls

        # 1. Update hull test designs
        print "Updating Testdesigns for hulls..."
        if verbose:
            print "Available Hulls: ", available_hulls
            print "Existing Designs (prefix: %s): " % TESTDESIGN_NAME_HULL,
            print [x.replace(TESTDESIGN_NAME_HULL, "") for x in testdesign_names_hull]
        for hull in [get_hulltype(hullname) for hullname in available_hulls
                     if "%s_%s" % (TESTDESIGN_NAME_HULL, hullname) not in testdesign_names_hull]:
            partlist = len(hull.slots) * [""]
            testdesign_name = "%s_%s" % (TESTDESIGN_NAME_HULL, hull.name)
            res = fo.issueCreateShipDesignOrder(testdesign_name, "TESTPURPOSE ONLY", hull.name,
                                                partlist, "", "fighter", False)
            if res:
                print "Success: Added Test Design %s, with result %d" % (testdesign_name, res)
            else:
                print "Error: When adding test design %s - got result %d but expected 1" % (testdesign_name, res)
                continue

        # 2. Cache the list of buildable ship hulls for each planet
        print "Caching buildable hulls per planet..."
        testname = "%s_%s" % (TESTDESIGN_NAME_HULL, "%s")
        for pid in planets_with_shipyards:
            self.hulls_for_planets[pid] = []
        for hullname in available_hulls:
            testdesign = _get_design_by_name(testname % hullname)
            if testdesign:
                for pid in planets_with_shipyards:
                    if _can_build(testdesign, empire_id, pid):
                        self.hulls_for_planets[pid].append(hullname)
            else:
                print "Missing testdesign for hull %s!" % hullname

        # 3. Update ship part test designs
        #     Because there are different slottypes, we need to find a hull that can host said slot.
        #     However, not every planet can buld every hull. Thus, for each planet with a shipyard:
        #       I. Check which parts do not have a testdesign yet with a hull we can build on this planet
        #       II. If there are parts, find out which slots we need
        #       III. For each slot type, try to find a hull we can build on this planet
        #            and use this hull for all the parts hostable in this type.
        print "Updating test designs for ship parts..."
        if verbose:
            print "Available parts: ", available_parts
            print "Existing Designs (prefix: %s): " % TESTDESIGN_NAME_PART,
            print [x.replace(TESTDESIGN_NAME_PART, "") for x in testdesign_names_part]
        for pid in planets_with_shipyards:
            planetname = universe.getPlanet(pid).name
            local_hulls = self.hulls_for_planets[pid]
            needs_update = [_get_part_type(partname) for partname in available_parts
                            if not any(["%s_%s_%s" % (TESTDESIGN_NAME_PART, partname, hullname) in testdesign_names_part
                                       for hullname in local_hulls])]
            if not needs_update:
                if verbose:
                    print "Planet %s: Test designs are up to date" % planetname
                continue
            if verbose:
                print "Planet %s: The following parts appear to need a new design: " % planetname,
                print [part.name for part in needs_update]
            for slot in available_slot_types:
                testhull = next((hullname for hullname in local_hulls if slot in get_hulltype(hullname).slots), None)
                if testhull is None:
                    if verbose:
                        print "Failure: Could not find a hull with slots of type '%s' for this planet" % slot.name
                    continue
                else:
                    if verbose:
                        print "Using hull %s for slots of type '%s'" % (testhull, slot.name)
                    self.testhulls.add(testhull)
                slotlist = [s for s in get_hulltype(testhull).slots]
                slot_index = slotlist.index(slot)
                num_slots = len(slotlist)
                for part in [part for part in needs_update if slot in part.mountableSlotTypes]:
                    partlist = num_slots * [""]
                    partlist[slot_index] = part.name
                    testdesign_name = "%s_%s_%s" % (TESTDESIGN_NAME_PART, part.name, testhull)
                    res = fo.issueCreateShipDesignOrder(testdesign_name, "TESTPURPOSE ONLY", testhull,
                                                        partlist, "", "fighter", False)
                    if res:
                        print "Success: Added Test Design %s, with result %d" % (testdesign_name, res)
                        testdesign_names_part.append(testdesign_name)
                    else:
                        print "Failure: Unknown error when adding test design %s" % testdesign_name,
                        print "got result %d but expected 1" % res
                        continue
                    needs_update.remove(part)  # We only need one design per part, not for every possible slot

        #  later on in the code, we will have to check multiple times if the test hulls are in
        #  the list of buildable hulls for the planet. As the ordering is preserved, move the
        #  testhulls to the front of the availableHull list to save some time in the checks.
        for i, s in enumerate(self.testhulls):
            try:
                idx = available_hulls.index(s)
                if i != idx:
                    available_hulls[i], available_hulls[idx] = available_hulls[idx], available_hulls[i]
            except ValueError:
                print "ERROR: hull in testhull cache not in available_hulls",
                print "eventhough it is supposed to be a proper subset."
                traceback.print_exc()
        number_of_testhulls = len(self.testhulls)

        # 4. Cache the list of buildable ship parts for each planet
        print "Caching buildable ship parts per planet..."
        for pid in planets_with_shipyards:
            local_testhulls = [hull for hull in self.testhulls
                               if hull in self.hulls_for_planets[pid][:number_of_testhulls]]
            self.parts_for_planets[pid] = {}
            local_ignore = set()
            local_cache = self.parts_for_planets[pid]
            for slot in available_slot_types:
                local_cache[slot] = []
            for partname in available_parts:
                if partname in local_ignore:
                    continue
                ship_design = None
                for hullname in local_testhulls:
                    ship_design = _get_design_by_name("%s_%s_%s" % (TESTDESIGN_NAME_PART, partname, hullname))
                    if ship_design:
                        if _can_build(ship_design, empire_id, pid):
                            for slot in _get_part_type(partname).mountableSlotTypes:
                                local_cache[slot].append(partname)
                                local_ignore.update(self.strictly_worse_parts[partname])
                        break
                if verbose and not ship_design:
                    planetname = universe.getPlanet(pid).name
                    print "Failure: Couldn't find a testdesign for part %s on planet %s." % (partname, planetname)
            # make sure we do not edit the list later on this turn => tuple: immutable
            # This also allows to shallowcopy the cache.
            for slot in local_cache:
                local_cache[slot] = tuple(local_cache[slot])

            if verbose:
                print "%s: " % universe.getPlanet(pid).name, self.parts_for_planets[pid]


Cache = ShipDesignCache()


class AdditionalSpecifications(object):
    """This class is a container for all kind of additional information
    and requirements we may want to use when assessing ship designs.

    methods for external use:
    convert_to_tuple(): Builds and returns a tuple of the class attributes
    update_enemy(enemy): updates enemy stats
    """

    def __init__(self):
        # TODO: Extend this framework according to needs of future implementations
        self.minimum_fuel = 0
        self.minimum_speed = 0
        self.minimum_structure = 1
        self.enemy_shields = 0
        self.enemy_weapon_strength = 0
        self.enemy_mine_dmg = 0  # TODO: Implement the detection of enemy mine damage
        self.update_enemy(foAI.foAIstate.empire_standard_enemy)

    def update_enemy(self, enemy):
        """Read out the enemies stats and save them.

        :param enemy: enemy as defined in AIstate
        """
        self.enemy_shields = enemy[2]
        enemy_attack_stats = enemy[1]
        self.enemy_weapon_strength = 0
        for stat in enemy_attack_stats:
            if stat[0] > self.enemy_weapon_strength:
                self.enemy_weapon_strength = stat[0]

    def convert_to_tuple(self):
        """Create a tuple of this class' attributes (e.g. to use as key in dict).

        :returns: tuple (minFuel,minSpeed,enemyDmg,enemyShield,enemyMineDmg)
        """
        return ("minFuel: %s" % self.minimum_fuel, "minSpeed: %s" % self.minimum_speed,
               "enemyDmg: %s" % self.enemy_weapon_strength, "enemyShields: %s" % self.enemy_shields,
               "enemyMineDmg: %s" % self.enemy_mine_dmg)


class ShipDesigner(object):
    """This class and its subclasses implement the building of a ship design and its rating.
     Specialised Designs with their own rating system or optimizing algorithms should inherit from this class.

    Member functions intended for external use:
    optimize_design(): Returns the estimated optimum design according to the rating function
    evaluate(): Returns a rating for the design as of the current state
    update_hull(hullname): sets the hull used in the design
    update_parts(partname_list): sets the parts used in the design
    update_species(speciesname): sets the piloting species
    update_stats(): calculates the stats of the design based on hull+parts+species
    add_design(): Adds the shipdesign in the C++ part of the game

    Functions which are to be overridden in inherited classes:
    _rating_function()
    _class_specific_filter()
    _starting_guess()
    _calc_rating_for_name()

    For improved performance, maybe override _filling_algorithm() with a more specialised algorithm as well.
    """
    basename = "Default - Do not build"     # base design name
    description = "Base class Ship type"    # design description
    useful_part_classes = ALL_META_CLASSES  # only these parts are considered in the design process

    filter_useful_parts = True              # removes any part not belonging to self.useful_part_classes
    filter_inefficient_parts = False        # removes cost-inefficient parts (less capacity and less capacity/cost)

    NAMETABLE = "AI_SHIPDESIGN_NAME_INVALID"
    NAME_THRESHOLDS = []                    # list of rating thresholds to choose a different name
    design_name_dict = {}                   # {min_rating: basename}: based on rating, the highest unlocked name is used
    running_index = {}                      # {basename: int}: a running index per design name

    def __init__(self):
        """Make sure to call this constructor in each subclass."""
        self.species = None         # name of the piloting species (string)
        self.hull = None            # hull object (not hullname!)
        self.partnames = []         # list of partnames (string)
        self.parts = []             # list of actual part objects
        self.attacks = {}           # {damage:count}
        self.structure = 0
        self.shields = 0
        self.fuel = 0
        self.speed = 0
        self.stealth = 0
        self.detection = 0
        self.troops = 0
        self.colonisation = -1      # -1 since 0 indicates an outpost (capacity = 0)
        self.production_cost = 9999
        self.production_time = 1
        self.pid = -1               # planetID for checks on production cost if not LocationInvariant.
        self.additional_specifications = AdditionalSpecifications()
        self.design_name_dict = {k: v for k, v in zip(self.NAME_THRESHOLDS,
                                                      UserString(self.NAMETABLE, self.basename).split())}

    def evaluate(self):
        """ Return a rating for the design.

        First, check if the minimum requirements are met. If so, return the _rating_function().
        Otherwise, return a large negative number scaling with distance to the requirements.

        :returns: float - rating of the current part/hull combo
        """
        self.update_stats()
        rating = 0

        # If we do not meet the requirements, we want to return a negative rating.
        # However, we also need to make sure, that the closer we are to requirements,
        # the better our rating is so the optimizing heuristic finds "the right way".
        if self.fuel < self.additional_specifications.minimum_fuel:
            rating += MISSING_REQUIREMENT_MULTIPLIER * (self.additional_specifications.minimum_fuel - self.fuel)
        if self.speed < self.additional_specifications.minimum_speed:
            rating += MISSING_REQUIREMENT_MULTIPLIER * (self.additional_specifications.minimum_speed - self.speed)
        if self.structure < self.additional_specifications.minimum_structure:
            rating += MISSING_REQUIREMENT_MULTIPLIER * (self.additional_specifications.minimum_structure - self.structure)
        if rating < 0:
            return rating
        else:
            return self._rating_function()

    def _rating_function(self):
        """Rate the design according to current hull/part combo.

        :returns: float - rating
        """
        print_error("WARNING: Rating function not overloaded for class %s!" % self.__class__.__name__)
        return -9999

    def _set_stats_to_default(self):
        """Set stats to default.

        Call this if design is invalid to avoid miscalculation of ratings."""
        self.structure = 0
        self.attacks.clear()
        self.shields = 0
        self.fuel = 0.0001
        self.speed = 0.0001
        self.stealth = 0.0001
        self.detection = 0.0001
        self.troops = 0
        self.colonisation = -1
        self.production_cost = 9999999
        self.production_time = 1

    def update_hull(self, hullname):
        """Set hull of the design.

        :param hullname:
        :type hullname: str
        """
        self.hull = fo.getHullType(hullname)

    def update_parts(self, partname_list):
        """Set both partnames and parts attributes.

        :param partname_list: contains partnames as strings
        :type partname_list: list"""
        self.partnames = partname_list
        self.parts = [_get_part_type(part) for part in partname_list if part]

    def update_species(self, speciesname):
        """Set the piloting species.

        :param speciesname:
        :type speciesname: str
        """
        self.species = speciesname

    def update_stats(self, ignore_species=False):
        """Calculate and update all stats of the design.

        Default stats if no hull in design.

        :param ignore_species: toggles whether species piloting grades are considered in the stats.
        :type ignore_species: bool
        """
        if not self.hull:
            print "WARNING: Tried to update stats of design without hull. Reset values to default."
            self._set_stats_to_default()
            return

        local_cost_cache = Cache.production_cost[self.pid]
        local_time_cache = Cache.production_time[self.pid]

        # read out hull stats
        self.structure = self.hull.structure
        self.fuel = self.hull.fuel
        self.speed = self.hull.speed
        self.stealth = self.hull.stealth
        self.attacks.clear()
        self.detection = 0  # TODO: Add self.hull.detection once available in interface
        self.shields = 0    # TODO: Add self.hull.shields if added to interface
        self.troops = 0     # TODO: Add self.hull.troops if added to interface
        self.production_cost = local_cost_cache[self.hull.name]
        self.production_time = local_time_cache[self.hull.name]
        self.colonisation = -1  # -1 as 0 corresponds to outpost pod (capacity = 0)

        # read out part stats
        shield_counter = cloak_counter = detection_counter = colonization_counter = 0  # to deal with Non-stacking parts
        for part in self.parts:
            self.production_cost += local_cost_cache[part.name]
            self.production_time = max(self.production_time, local_time_cache[part.name])
            partclass = part.partClass
            capacity = part.capacity
            if partclass in FUEL:
                self.fuel += capacity
            elif partclass in ENGINES:
                self.speed += capacity
            elif partclass in COLONISATION:
                colonization_counter += 1
                if colonization_counter == 1:
                    self.colonisation = capacity
                else:
                    self.colonisation = -1
            elif partclass in DETECTION:
                detection_counter += 1
                if detection_counter == 1:
                    self.detection += capacity
                else:
                    self.detection = 0
            elif partclass in ARMOUR:
                self.structure += capacity
            elif partclass in WEAPONS:
                if capacity in self.attacks:
                    self.attacks[capacity] += 1
                else:
                    self.attacks[capacity] = 1
            elif partclass in SHIELDS:
                shield_counter += 1
                if shield_counter == 1:
                    self.shields = capacity
                else:
                    self.shields = 0
            elif partclass in TROOPS:
                self.troops += capacity
            elif partclass in STEALTH:
                cloak_counter += 1
                if cloak_counter == 1:
                    self.stealth += capacity
                else:
                    self.stealth = 0
            # TODO: (Hardcode?) extra effect modifiers such as the transspatial drive or multispectral shields, ...

        if self.species and not ignore_species:
            # TODO: Add troop modifiers once added
            weapons_grade, shields_grade = foAI.foAIstate.get_piloting_grades(self.species)
            self.shields = foAI.foAIstate.weight_shields(self.shields, shields_grade)
            if self.attacks:
                self.attacks = foAI.foAIstate.weight_attacks(self.attacks, weapons_grade)

    def add_design(self, verbose=False):
        """Add a real (i.e. gameobject) ship design of the current configuration.

        :param verbose: toggles detailed debugging output
        :type verbose: bool
        """
        # First build a name. We want to have a safe way to reference the design
        # And to find out whether it is a duplicate of an existing one.
        # Therefore, we build a reference name using the hullname and all parts.
        # The real name that is shown in the game AI differs from that one. Current
        # implementation is a simple running index that gets counted up in addition
        # to a base name. The real name is mapped using a dictionary.
        # For now, abbreviating the Empire name to uppercase first and last initials

        design_name = self._build_design_name()
        reference_name = _build_reference_name(self.hull.name, self.partnames)  # "Hull-Part1-Part2-Part3-Part4"

        if reference_name in Cache.map_reference_design_name:
            if verbose:
                print "Design already exists"
            try:
                return _get_design_by_name(Cache.map_reference_design_name[reference_name]).id
            except AttributeError:
                cached_name = Cache.map_reference_design_name[reference_name]
                print "ERROR: %s maps to %s in Cache.map_reference_design_name." % (reference_name, cached_name),
                print "But the design seems not to exist..."
                traceback.print_exc()
                return None

        if verbose:
            print "Trying to add Design... %s" % design_name
        res = fo.issueCreateShipDesignOrder(design_name, self.description, self.hull.name,
                                            self.partnames, "", "fighter", False)
        if res:
            if verbose:
                print "Success: Added Design %s, with result %d" % (design_name, res)
        else:
            print "Failure: Tried to add design %s but returned %d, expected 1" % (design_name, res)
            return None
        new_id = _get_design_by_name(design_name).id
        if new_id:
            Cache.map_reference_design_name[reference_name] = design_name
            return new_id

    def _class_specific_filter(self, partname_dict):
        """Add additional filtering to _filter_parts().

        To be implemented in subclasses.
        """
        pass

    def optimize_design(self, loc=None, verbose=False):
        """Try to find the optimimum designs for the shipclass for each planet and add it as gameobject.

        Only designs with a positive rating (i.e. matching the minimum requirements) will be returned.

        :return: list of (rating,planet_id,design_id,cost) tuples, i.e. best available design for each planet
        :param loc: int or list of ints (optional) - planet ids where the designs are to be built. Default: All planets.
        :param verbose: Toggles detailed logging for debugging.
        :type verbose: bool
        """
        if loc is None:
            planets = _get_planets_with_shipyard()
        elif isinstance(loc, int):
            planets = [loc]
        elif isinstance(loc, list):
            planets = loc
        else:
            print "ERROR: Invalid loc parameter for optimize_design(). Expected int or list but got", loc
            return []
        design_cache_class = Cache.best_designs.setdefault(self.__class__.__name__, {})
        req_tuple = self.additional_specifications.convert_to_tuple()
        design_cache_reqs = design_cache_class.setdefault(req_tuple, {})
        universe = fo.getUniverse()
        best_design_list = []

        print "----------"
        print "Trying to find optimum designs for shiptype class %s" % self.__class__.__name__
        for pid in planets:
            planet = universe.getPlanet(pid)
            self.pid = pid
            self.update_species(planet.speciesName)
            # The piloting species is only important if its modifiers are of any use to the design
            # Therefore, consider only those treats that are actually useful. Note that the
            # canColonize trait is covered by the parts we can build, so no need to consider it here.
            weapons_grade, shields_grade = foAI.foAIstate.get_piloting_grades(self.species)
            relevant_grades = []
            if WEAPONS & self.useful_part_classes:
                relevant_grades.append("WEAPON: %s" % weapons_grade)
            if SHIELDS & self.useful_part_classes:
                relevant_grades.append("SHIELDS: %s" % shields_grade)
            # TODO: Add troop modifiers once added
            species_tuple = tuple(relevant_grades)

            design_cache_species = design_cache_reqs.setdefault(species_tuple, {})
            available_hulls = Cache.hulls_for_planets[pid]
            if verbose:
                print "Evaluating planet %s" % planet.name
                print "Species:", planet.speciesName
                print "Available Ship Hulls: ", available_hulls
            available_parts = copy.copy(Cache.parts_for_planets[pid])  # this is a dict! {slottype:(partnames)}
            for slot in available_parts:
                available_parts[slot] = list(available_parts[slot])
            self._filter_parts(available_parts, verbose=verbose)
            all_parts = []
            for partlist in available_parts.values():
                all_parts += partlist
            design_cache_parts = design_cache_species.setdefault(frozenset(all_parts), {})
            best_rating_for_planet = 0
            best_hull = None
            best_parts = None
            for hullname in available_hulls:
                if hullname in design_cache_parts:
                    cache = design_cache_parts[hullname]
                    best_hull_rating = cache[0]
                    current_parts = cache[1]
                    if verbose:
                        print "Best rating for hull %s: %f (read from Cache)" % (hullname, best_hull_rating),
                        print current_parts
                else:
                    self.update_hull(hullname)
                    best_hull_rating, current_parts = self._filling_algorithm(available_parts)
                    design_cache_parts.update({hullname: (best_hull_rating, current_parts)})
                    if verbose:
                        print "Best rating for hull %s: %f" % (hullname, best_hull_rating), current_parts
                if best_hull_rating > best_rating_for_planet:
                    best_rating_for_planet = best_hull_rating
                    best_hull = hullname
                    best_parts = current_parts
            if verbose:
                print "Best overall rating for this planet: %f" % best_rating_for_planet,
                print "(", best_hull, " with", best_parts, ")"
            if best_hull:
                self.update_hull(best_hull)
                self.update_parts(best_parts)
                design_id = self.add_design()
                if design_id:
                    best_design_list.append((best_rating_for_planet, pid, design_id, self.production_cost))
                else:
                    print_error("The best design for %s on planet %d could not be added."
                                % (self.__class__.__name__, pid))
            else:
                print "Could not find a suitable design for this planet."
        sorted_design_list = sorted(best_design_list, key=lambda x: x[0], reverse=True)
        return sorted_design_list

    def _filter_parts(self, partname_dict, verbose=False):
        """Filter the partname_dict.

        This function filters a list of parts according to the following criteria:

            1) filter out parts not in self.useful_part_classes
            2) filter_inefficient_parts (optional): filters out parts that are weaker and have a worse effect/cost ratio
            3) ship class specific filter as defined in _class_specific_filter

        Each filter can be turned on/off by setting the correspondig class attribute to true/false.
        WARNING: The dict passed as parameter is modified inside this function and entries are removed!

        :param partname_dict: keys: slottype, values: list of partnames. MODIFIED INSIDE THIS FUNCTION!
        :param verbose: toggles verbose logging
        :type verbose: bool
        """
        if verbose:
            print "Available parts:"
            for x in partname_dict:
                print x, ":", partname_dict[x]

        part_dict = {slottype: zip(partname_dict[slottype], map(_get_part_type, partname_dict[slottype]))
                     for slottype in partname_dict}  # {slottype: [(partname, parttype_object)]}

        for slottype in part_dict:
            part_dict[slottype] = [tup for tup in part_dict[slottype] if tup[1].partClass in self.useful_part_classes]

        if self.filter_inefficient_parts:
            local_cost_cache = Cache.production_cost[self.pid]
            check_for_redundance = (WEAPONS | ARMOUR | ENGINES | FUEL | SHIELDS
                                    | STEALTH | DETECTION | TROOPS) & self.useful_part_classes
            for slottype in part_dict:
                partclass_dict = defaultdict(list)
                for tup in part_dict[slottype]:
                    partclass = tup[1].partClass
                    if partclass in check_for_redundance:
                        partclass_dict[partclass].append(tup[1])
                for shipPartsPerClass in partclass_dict.itervalues():
                    for a in shipPartsPerClass:
                        if a.capacity == 0:  # TODO: Modify this if effects of items get hardcoded
                            part_dict[slottype].remove((a.name, a))
                            if verbose:
                                print "removing %s because capacity is zero." % a.name
                            continue
                        if len(shipPartsPerClass) == 1:
                            break
                        cost_a = local_cost_cache[a.name]
                        for b in shipPartsPerClass:
                            if (b is not a
                                    and (b.capacity/local_cost_cache[b.name] - a.capacity/cost_a) > -1e-6
                                    and b.capacity >= a.capacity):
                                if verbose:
                                    print "removing %s because %s is better." % (a.name, b.name)
                                part_dict[slottype].remove((a.name, a))
                                break
        for slottype in part_dict:
            partname_dict[slottype] = [tup[0] for tup in part_dict[slottype]]
        self._class_specific_filter(partname_dict)
        if verbose:
            print "Available parts after filtering:"
            for x in partname_dict:
                print x, ":", partname_dict[x]

    def _starting_guess(self, available_parts, num_slots):
        """Return an initial guess for the filling of the slots.

        The closer the guess to the final result, the less time the optimizing algorithm takes to finish.
        In order to improve performance it thus makes sense to state a very informed guess so only a few
        parts if any have to be changed.

        If not overloaded in the subclasses, the initial guess is an empty design.

        :param available_parts: name of the available parts including "" for empty slot (last position of the list)
        :param num_slots: number of slots to fill
        :return: list of int: the number of parts used in the design (indexed in order as in available_parts)
        """
        return len(available_parts)*[0]+[num_slots]  # corresponds to an entirely empty design

    def _combinatorial_filling(self, available_parts):
        """Fill the design using a combinatorial approach.

        This generic filling algorithm considers the problem of filling the slots as combinatorial problem.
        We are interested in the best combination of parts without considering order.
        In general, this yields (n+k-1) choose k possibilities in total per slottype where
        n is the number of parts and k is the number of slots.
        For s different slottypes, we thus end up with Product((n_i+k_i-1) choose k_i, i=1..s)
        combinations to test for. As this is still quite too much for brute force, we use the following heuristic:

        find some _starting_guess() for the filling
        until no more improvement:
            for each slottype in the hull:
                until no more improvement:
                    swap single parts if this increases the rating

        So basically optimize the filling for each slottype sequentially. If one slottype was improved, we need to
        check the already optimized slottypes again as the new parts could have affected the value of some parts.
        The same logic holds true for the parts: If we tried to exchange two parts earlier which did not improve
        the rating, we can't be sure that still isn't the case if we swapped some other parts. For example, consider
        a military ship without weapons: The rating will always be zero. So swapping a bad armour part for a good one
        will not yield an improvement. Once we add a single weapon however, the rating will be increased by exchanging
        the armour parts.

        This heuristic will always find a local maximum. For simple enough (convex) rating functions this is also
        the global maximum. More intrigued functions might require a different approach, however. Another problem might
        occur if we have a non-stacking part available for both the external and internal slot. We will never exchange
        these parts in this algorithm so if future oontent has this situation, we need to either specify a very distinct
        _starting_guess() or change the algorithm.

        :param available_parts: dict, indexed by slottype, containing a list of partnames for the slot
        :return: best rating, corresponding list of partnames
        """
        number_of_slots_by_slottype = Counter()
        for slot in self.hull.slots:
            number_of_slots_by_slottype[slot] += 1
        parts = {}                # indexed by slottype, contains list of partnames (list of strings)
        total_filling = {}        # indexed by slottype, contains the number of parts (ordered as in parts)
        for slot in number_of_slots_by_slottype:
            parts[slot] = available_parts[slot] + [""]
            total_filling[slot] = self._starting_guess(available_parts[slot], number_of_slots_by_slottype[slot])
        last_changed_slottype = None
        exit_outer_loop = False
        while True:
            # Try to optimize each slottype iteratively until no more improvement.
            if exit_outer_loop:
                break
            for slot in number_of_slots_by_slottype:
                if last_changed_slottype is None:  # first run of the loop
                    last_changed_slottype = slot
                elif slot == last_changed_slottype:
                    exit_outer_loop = True
                    break
                current_filling = total_filling[slot]
                num_parts = len(current_filling)
                range_parts = range(num_parts)
                current_parts = []
                other_parts = []
                for s in number_of_slots_by_slottype:
                    if s is slot:
                        for j in range_parts:
                            current_parts += current_filling[j] * [parts[s][j]]
                    else:
                        for j in xrange(len(total_filling[s])):
                            other_parts += total_filling[s][j] * [parts[s][j]]
                self.update_parts(other_parts+current_parts)
                current_rating = self.evaluate()
                last_changed = None
                exit_loop = False
                while not exit_loop:
                    # Try to increase the count of one part and decreasing the other parts
                    # as long as this yields a better rating. Repeat until no more improvement.
                    for i in range_parts:
                        if i == last_changed:
                            exit_loop = True
                            break
                        elif last_changed is None:  # first run of the loop
                            last_changed = i
                        for j in range_parts:
                            if j == i:
                                continue
                            while current_filling[j] > 0:
                                current_parts[current_parts.index(parts[slot][j])] = parts[slot][i]  # exchange parts
                                self.update_parts(other_parts+current_parts)
                                new_rating = self.evaluate()
                                if new_rating > current_rating:  # keep the new config as it is better.
                                    current_filling[j] -= 1
                                    current_filling[i] += 1
                                    current_rating = new_rating
                                    last_changed = i
                                    last_changed_slottype = slot
                                else:  # undo the change as the rating is worse, try next part.
                                    current_parts[current_parts.index(parts[slot][i])] = parts[slot][j]
                                    break
        # rebuild the partlist in the order of the slots of the hull
        partlist = []
        slot_filling = {}
        for slot in number_of_slots_by_slottype:
            slot_filling[slot] = []
            for j in xrange(len(total_filling[slot])):
                slot_filling[slot] += total_filling[slot][j] * [parts[slot][j]]
        for slot in self.hull.slots:
            partlist.append(slot_filling[slot].pop())
        self.update_parts(partlist)
        rating = self.evaluate()
        return rating, partlist

    def _filling_algorithm(self, available_parts):
        """Fill the slots of the design using some optimizing algorithm.

        Default algorithm is _combinatorial_filling().

        :param available_parts: dict, indexed by slottype, containing a list of partnames for the slot
        """
        rating, parts = self._combinatorial_filling(available_parts)
        return rating, parts

    def _total_dmg_vs_shields(self):
        """Sum up and return the damage of weapon parts vs a shielded enemy as defined in additional_specifications.

        :return: summed up damage vs shielded enemy
        """
        total_dmg = 0
        for dmg, count in self.attacks.items():
            total_dmg += max(0, dmg - self.additional_specifications.enemy_shields)*count
        return total_dmg

    def _total_dmg(self):
        """Sum up and return the damage of all weapon parts.

        :return: Total damage of the design (against no shields)
        """
        total_dmg = 0
        for dmg, count in self.attacks.items():
            total_dmg += dmg*count
        return total_dmg

    def _build_design_name(self):
        """Build the ingame design name.

        The design name is based on empire name, shipclass and, if a design_name_dict is implemented for this class,
        on the strength of the design.
        :return: string
        """
        name_template = "%s %s Mk. %d"  # e.g. "EmpireAbbreviation Warship Mk. 1"
        empire_name = fo.getEmpire().name.upper()
        empire_initials = empire_name[:1] + empire_name[-1:]
        rating = self._calc_rating_for_name()
        basename = next((name for (maxRating, name) in sorted(self.design_name_dict.items(), reverse=True)
                        if rating > maxRating), self.__class__.basename)

        def design_name():
            """return the design name based on the name_template"""
            return name_template % (empire_initials, basename, self.running_index[basename])
        self.__class__.running_index.setdefault(basename, 1)
        while _get_design_by_name(design_name()):
            self.__class__.running_index[basename] += 1
        return design_name()

    def _calc_rating_for_name(self):
        """Return a rough rating for the design independent of special requirements and species.

         The design name should not depend on the strength of the enemy or upon some additional requests we have
         for the design but only compare the basic functionality. If not overloaded in the subclass, this function
         returns the structure of the design.

         :return: float - a rough approximation of the strength of this design
         """
        self.update_stats(ignore_species=True)
        return self.structure


class MilitaryShipDesigner(ShipDesigner):
    """Class that implements military designs.

    Extends __init__()
    Overrides _rating_function()
    Overrides _starting_guess()
    """
    basename = "Warship"
    description = "Military Ship"
    useful_part_classes = ARMOUR | WEAPONS | SHIELDS | FUEL | ENGINES
    filter_useful_parts = True
    filter_inefficient_parts = True

    NAMETABLE = "AI_SHIPDESIGN_NAME_MILITARY"
    NAME_THRESHOLDS = sorted([0, 100, 250, 500, 1000, 2500, 5000, 7500, 10000,
                              15000, 20000, 25000, 30000, 35000, 40000, 45000, 50000, 60000])

    def __init__(self):
        ShipDesigner.__init__(self)
        self.additional_specifications.minimum_fuel = 1
        self.additional_specifications.minimum_speed = 30

    def _rating_function(self):
        # TODO: Find a better way to determine the value of speed and fuel
        enemy_dmg = self.additional_specifications.enemy_weapon_strength
        total_dmg = max(self._total_dmg_vs_shields(), 0.1)
        shield_factor = max(enemy_dmg / max(0.01, enemy_dmg - self.shields), 1)
        effective_structure = self.structure * shield_factor
        speed_factor = 1 + 0.003*(self.speed - 85)
        fuel_factor = 1 + 0.03 * (self.fuel - self.additional_specifications.minimum_fuel) ** 0.5
        return max(total_dmg, 0.1) * effective_structure * speed_factor * fuel_factor / self.production_cost

    def _starting_guess(self, available_parts, num_slots):
        # for military ships, our primary rating function is given by
        # [n*d * (a*(s-n) + h)] / [n*cw + (s-n) * ca + ch]
        # where:
        # s = number of slots
        # n = number of slots filled with weapons
        # d = damage of the weapon
        # a = structure value of armour parts
        # h = base structure of the hull
        # cw, ca, ch = cost of weapon, armour, hull respectively
        # As this is a simple rational function in n, the maximizing problem can be solved analytically.
        # The analytical solution (after rounding to the nearest integer)is a good starting guess for our best design.
        ret_val = (len(available_parts)+1)*[0]
        parts = [_get_part_type(part) for part in available_parts]
        weapons = [part for part in parts if part.partClass in WEAPONS]
        armours = [part for part in parts if part.partClass in ARMOUR]
        cap = lambda x: x.capacity
        if weapons:
            weapon = max(weapons, key=cap).name
            idxweapon = available_parts.index(weapon)
            cw = Cache.production_cost[self.pid][weapon]
            if armours:
                armour = max(armours, key=cap).name
                idxarmour = available_parts.index(armour)
                a = _get_part_type(armour).capacity
                ca = Cache.production_cost[self.pid][armour]
                s = num_slots
                h = self.hull.structure
                ch = Cache.production_cost[self.pid][self.hull.name]
                p1 = a*s*ca + a*ch
                p2 = math.sqrt(a * (ca*s + ch) * (a*s*cw+a*ch+h*cw-h*ca))
                p3 = a*(ca-cw)
                n = max((p1+p2)/p3, (p1-p2)/p3)
                n = int(round(n))
                n = max(n, 1)
                n = min(n, s)
                print "estimated weapon slots for %s: %d" % (self.hull.name, n)
                ret_val[idxarmour] = s-n
                ret_val[idxweapon] = n
            else:
                ret_val[idxweapon] = num_slots
        elif armours:
            armour = max(armours, key=cap).name
            idxarmour = available_parts.index(armour)
            ret_val[idxarmour] = num_slots
        else:
            ret_val[-1] = num_slots
        return ret_val

    def _calc_rating_for_name(self):
        self.update_stats(ignore_species=True)
        return self.structure*self._total_dmg()*(1+self.shields/10)


class TroopShipDesignerBaseClass(ShipDesigner):
    """Base class for troop ships. To be inherited from.

    Extends __init__()
    Overrides _rating_function()
    Overrides _starting_guess()
    Overrides _class_specific_filter
    """
    basename = "Troopers (Do not build me)"
    description = "Trooper."
    useful_part_classes = TROOPS
    filter_useful_parts = True
    filter_inefficient_parts = True

    def __init__(self):
        ShipDesigner.__init__(self)
        self.additional_specifications.minimum_structure = self.additional_specifications.enemy_mine_dmg * 2

    def _rating_function(self):
        if self.troops == 0:
            return INVALID_DESIGN_RATING
        else:
            return self.troops/self.production_cost

    def _starting_guess(self, available_parts, num_slots):
        # fill completely with biggest troop pods. If none are available for this slot type, leave empty.
        troop_pods = [_get_part_type(part) for part in available_parts if _get_part_type(part).partClass in TROOPS]
        ret_val = (len(available_parts)+1)*[0]
        if troop_pods:
            cap = lambda x: x.capacity
            biggest_troop_pod = max(troop_pods, key=cap).name
            try:  # we could use an if-check here but since we usually have troop pods for the slot, try is faster
                idx = available_parts.index(biggest_troop_pod)
            except ValueError:
                idx = len(available_parts)
                traceback.print_exc()
        else:
            idx = len(available_parts)
        ret_val[idx] = num_slots
        return ret_val

    def _class_specific_filter(self, partname_dict):
        for slot in partname_dict:
            remaining_parts = [part for part in partname_dict[slot] if _get_part_type(part).partClass in TROOPS]
            partname_dict[slot] = remaining_parts


class OrbitalTroopShipDesigner(TroopShipDesignerBaseClass):
    """Class implementing orbital invasion designs

    Extends __init__()
    """
    basename = "SpaceInvaders"
    description = "Ship designed for local invasions of enemy planets"

    useful_part_classes = TROOPS
    NAMETABLE = "AI_SHIPDESIGN_NAME_TROOPER_ORBITAL"
    NAME_THRESHOLDS = sorted([0])

    def __init__(self):
        TroopShipDesignerBaseClass.__init__(self)
        self.additional_specifications.minimum_speed = 0
        self.additional_specifications.minimum_fuel = 0


class StandardTroopShipDesigner(TroopShipDesignerBaseClass):
    """Class implementing standard troop ship designs.

    Extends __init__()
    """
    basename = "StormTroopers"
    description = "Ship designed for the invasion of enemy planets"
    useful_part_classes = TROOPS
    NAMETABLE = "AI_SHIPDESIGN_NAME_TROOPER_STANDARD"
    NAME_THRESHOLDS = sorted([0])

    def __init__(self):
        TroopShipDesignerBaseClass.__init__(self)
        self.additional_specifications.minimum_speed = 30
        self.additional_specifications.minimum_fuel = 2


class ColonisationShipDesignerBaseClass(ShipDesigner):
    """Base class for colonization ships. To be inherited from.

    Extends __init__()
    Overrides _rating_function()
    Overrides _starting_guess()
    Overrides _class_specific_filter()
    """
    basename = "Seeder (Do not build me!)"
    description = "Unarmed Colony Ship"
    useful_part_classes = FUEL | COLONISATION | ENGINES | DETECTION

    filter_useful_parts = True
    filter_inefficient_parts = True

    def __init__(self):
        ShipDesigner.__init__(self)

    def _rating_function(self):
        if self.colonisation <= 0:  # -1 indicates no pod, 0 indicates outpost
            return INVALID_DESIGN_RATING
        return self.colonisation*(1+0.002*(self.speed-75))/self.production_cost

    def _starting_guess(self, available_parts, num_slots):
        # we want to use one and only one of the best colo pods
        ret_val = (len(available_parts)+1)*[0]
        if num_slots == 0:
            return ret_val
        parts = [_get_part_type(part) for part in available_parts]
        colo_parts = [part for part in parts if part.partClass in COLONISATION and part.capacity > 0]
        if colo_parts:
            colo_part = max(colo_parts, key=lambda x: x.capacity)
            idx = available_parts.index(colo_part.name)
            ret_val[idx] = 1
            ret_val[-1] = num_slots - 1
        else:
            ret_val[-1] = num_slots
        return ret_val

    def _class_specific_filter(self, partname_dict):
        # remove outpost pods
        for slot in partname_dict:
            parts = [_get_part_type(part) for part in partname_dict[slot]]
            for part in parts:
                if part.partClass in COLONISATION and part.capacity == 0:
                    partname_dict[slot].remove(part.name)


class StandardColonisationShipDesigner(ColonisationShipDesignerBaseClass):
    """Class that implements standard colonisation ships.

    Extends __init__()
    """
    basename = "Seeder"
    description = "Unarmed ship designed for the colonisation of distant planets"
    useful_part_classes = FUEL | COLONISATION | ENGINES | DETECTION
    NAMETABLE = "AI_SHIPDESIGN_NAME_COLONISATION_STANDARD"
    NAME_THRESHOLDS = sorted([0])

    def __init__(self):
        ColonisationShipDesignerBaseClass.__init__(self)
        self.additional_specifications.minimum_speed = 30
        self.additional_specifications.minimum_fuel = 1


class OrbitalColonisationShipDesigner(ColonisationShipDesignerBaseClass):
    """Class implementing orbital colonisation ships.

    Extends __init__()
    Overrides _rating_function()
    """
    basename = "Orbital Seeder"
    description = "Unarmed ship designed for the colonisation of local planets"
    useful_part_classes = COLONISATION
    NAMETABLE = "AI_SHIPDESIGN_NAME_COLONISATION_ORBITAL"
    NAME_THRESHOLDS = sorted([0])

    def __init__(self):
        ColonisationShipDesignerBaseClass.__init__(self)
        self.additional_specifications.minimum_speed = 0
        self.additional_specifications.minimum_fuel = 0

    def _rating_function(self):
        if self.colonisation <= 0:  # -1 indicates no pod, 0 indicates outpost
            return INVALID_DESIGN_RATING
        return self.colonisation/self.production_cost


class OutpostShipDesignerBaseClass(ShipDesigner):
    """Base class for outposter designs. To be inherited.

    Extends __init__()
    Overrides _rating_function()
    Overrides _starting_guess()
    Overrides _class_specific_filter()
    """
    basename = "Outposter (do not build me!)"
    description = "Unarmed Outposter Ship"
    useful_part_classes = COLONISATION | FUEL | ENGINES | DETECTION

    filter_useful_parts = True
    filter_inefficient_parts = True

    def __init__(self):
        ShipDesigner.__init__(self)

    def _rating_function(self):
        if self.colonisation != 0:
            return INVALID_DESIGN_RATING
        return (1+0.002*(self.speed-75))/self.production_cost

    def _class_specific_filter(self, partname_dict):
        # filter all colo pods
        for slot in partname_dict:
            parts = [_get_part_type(part) for part in partname_dict[slot]]
            for part in parts:
                if part.partClass in COLONISATION and part.capacity != 0:
                    partname_dict[slot].remove(part.name)

    def _starting_guess(self, available_parts, num_slots):
        # use one outpost pod as starting guess
        ret_val = (len(available_parts)+1)*[0]
        if num_slots == 0:
            return ret_val
        parts = [_get_part_type(part) for part in available_parts]
        colo_parts = [part for part in parts if part.partClass in COLONISATION and part.capacity == 0]
        if colo_parts:
            colo_part = colo_parts[0]
            idx = available_parts.index(colo_part.name)
            ret_val[idx] = 1
            ret_val[-1] = num_slots - 1
        else:
            ret_val[-1] = num_slots
        return ret_val


class OrbitalOutpostShipDesigner(OutpostShipDesignerBaseClass):
    """Class that implements orbital outposting ships.

    Extends __init__()
    Overrides _rating_function()
    """
    basename = "OrbitalOutposter"
    description = "Unarmed ship designed for founding local outposts"
    useful_part_classes = COLONISATION
    filter_useful_parts = True
    filter_inefficient_parts = False
    NAMETABLE = "AI_SHIPDESIGN_NAME_OUTPOSTER_ORBITAL"
    NAME_THRESHOLDS = sorted([0])

    def __init__(self):
        OutpostShipDesignerBaseClass.__init__(self)
        self.additional_specifications.minimum_fuel = 0
        self.additional_specifications.minimum_speed = 0

    def _rating_function(self):
        if self.colonisation != 0:
            return INVALID_DESIGN_RATING
        return 1/self.production_cost


class StandardOutpostShipDesigner(OutpostShipDesignerBaseClass):
    """Class that implements standard outposting ships.

    Extends __init__()
    """
    basename = "Outposter"
    description = "Unarmed ship designed for founding distant outposts"
    useful_part_classes = COLONISATION | FUEL | ENGINES | DETECTION
    NAMETABLE = "AI_SHIPDESIGN_NAME_OUTPOSTER_STANDARD"
    NAME_THRESHOLDS = sorted([0])

    def __init__(self):
        OutpostShipDesignerBaseClass.__init__(self)
        self.additional_specifications.minimum_fuel = 2
        self.additional_specifications.minimum_speed = 30


class OrbitalDefenseShipDesigner(ShipDesigner):
    """Class that implements orbital defense designs.

    Extends __init__()
    Overrides _rating_function()
    """
    basename = "Decoy"
    description = "Orbital Defense Ship"
    useful_part_classes = WEAPONS | ARMOUR
    NAMETABLE = "AI_SHIPDESIGN_NAME_ORBITAL_DEFENSE"
    NAME_THRESHOLDS = sorted([0, 1])

    filter_useful_parts = True
    filter_inefficient_parts = True

    def __init__(self):
        ShipDesigner.__init__(self)

    def _rating_function(self):
        if self.speed > 10:
            return INVALID_DESIGN_RATING
        total_dmg = self._total_dmg_vs_shields()
        return (1+total_dmg*self.structure)/self.production_cost

    def _calc_rating_for_name(self):
        self.update_stats(ignore_species=True)
        return self._total_dmg()


class ScoutShipDesigner(ShipDesigner):
    """Scout ship class"""
    basename = "Scout"
    description = "For exploration and reconnaissance"
    useful_part_classes = DETECTION | FUEL
    NAMETABLE = "AI_SHIPDESIGN_NAME_SCOUT"
    NAME_THRESHOLDS = sorted([0])
    filter_useful_parts = True
    filter_inefficient_parts = True

    def __init__(self):
        ShipDesigner.__init__(self)
        self.additional_specifications.minimum_fuel = 3
        self.additional_specifications.minimum_speed = 60

    def _rating_function(self):
        if not self.detection:
            return INVALID_DESIGN_RATING
        return self.detection**2 * self.fuel / self.production_cost


def _get_planets_with_shipyard():
    """Get all planets with shipyards.

    :return: list of planet_ids"""
    return ColonisationAI.empire_shipyards


def _get_design_by_name(design_name):
    """Return the shipDesign object of the design with the name design_name.

    Results are cached for performance improvements. The cache is to be
    checked for consistency with check_cache_for_consistency() once per turn
    as there appears to be a random bug in multiplayer, changing IDs.
    
    :param design_name: string
    :return: shipDesign object
    """
    if design_name in Cache.design_id_by_name:
        design = fo.getShipDesign(Cache.design_id_by_name[design_name])
        return design
    else:
        design = None
        for ID in fo.getEmpire().allShipDesigns:
            if fo.getShipDesign(ID).name(False) == design_name:
                design = fo.getShipDesign(ID)
                break
        if design:
            Cache.design_id_by_name[design_name] = design.id
        return design


def _get_part_type(partname):
    """Return the partType object (fo.getPartType(partname)) of the given partname.

    As the function in lategame may be called some thousand times, the results are cached.

    :param partname: string
    :returns:        partType object
    """
    if partname in Cache.part_by_partname:
        return Cache.part_by_partname[partname]
    else:
        parttype = fo.getPartType(partname)
        if parttype:
            Cache.part_by_partname[partname] = parttype
            return Cache.part_by_partname[partname]
        else:
            print "FAILURE: Could not find part", partname
            return None


def _build_reference_name(hullname, partlist):
    """
    This reference name is used to identify existing designs and is mapped
    by Cache.map_reference_design_name to the ingame design name. Order of components are ignored.

    :param hullname: hull name
    :type hullname: str
    :param partlist: list of part names
    :type partlist: list
    :return: reference name
    :rtype: str
    """
    return "%s-%s" % (hullname, "-".join(sorted(partlist)))  # "Hull-Part1-Part2-Part3-Part4"


def _can_build(design, empire_id, pid):
    # TODO: Remove this function once we stop profiling this module
    """Check if a design can be built by an empire on a particular planet.

    This function only exists for profiling reasons to add an extra entry to cProfile.

    :param design: design object
    :param empire_id:
    :param pid: id of the planet for which the check is performed
    :return: bool
    """
    return design.productionLocationForEmpire(empire_id, pid)
