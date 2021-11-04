# Copyright (c) Facebook, Inc. and its affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import ctypes
import sys
from typing import Any, Callable, Dict, Optional

flags = sys.getdlopenflags()
sys.setdlopenflags(flags | ctypes.RTLD_GLOBAL)

import magnum as mn
from magnum.platform.glfw import Application
from viewer import HabitatSimInteractiveViewer, MouseMode

import habitat_sim.physics as phy
from examples.fairmotion_interface import FairmotionInterface
from examples.settings import default_sim_settings
from habitat_sim.logging import logger


class FairmotionSimInteractiveViewer(HabitatSimInteractiveViewer):
    def __init__(
        self, sim_settings: Dict[str, Any], fm_settings: Dict[str, Any]
    ) -> None:
        super().__init__(sim_settings)

        # fairmotion init
        self.fm_demo = FairmotionInterface(
            self.sim,
            metadata_name="fm_demo",
            amass_path=fm_settings["amass_path"],
            metadata_dir=fm_settings["metadata_dir"],
        )

        # configuring MOTION display objects
        # selection sphere icon
        obj_tmp_mgr = self.sim.get_object_template_manager()
        self.sphere_template_id = obj_tmp_mgr.load_configs(
            "../habitat-sim/data/test_assets/objects/sphere"
        )[0]
        sphere_template = obj_tmp_mgr.get_template_by_id(self.sphere_template_id)
        sphere_template.scale = [0.30, 0.30, 0.30]
        obj_tmp_mgr.register_template(sphere_template)

        # selection origin box
        self.box_template_id = obj_tmp_mgr.load_configs(
            "../habitat-sim/data/test_assets/objects/nested_box"
        )[0]
        box_template = obj_tmp_mgr.get_template_by_id(self.box_template_id)
        box_template.scale = [0.15, 0.025, 2.5]
        obj_tmp_mgr.register_template(box_template)

        # motion mode attributes
        self.selected_mocap_char: Optional[FairmotionInterface] = None
        self.select_sphere_obj_id: int = -1
        self.select_box_obj_id: int = -1

    def draw_event(self, simulation_call: Optional[Callable] = None) -> None:
        """
        Calls continuously to re-render frames and swap the two frame buffers
        at a fixed rate. Use `simulation_call` to perform method calls during
        a simulation step.
        """

        def play_motion() -> None:
            if self.fm_demo.motion is not None:
                self.fm_demo.next_pose()
                self.fm_demo.next_pose()

        super().draw_event(simulation_call=play_motion)

    def key_press_event(self, event: Application.KeyEvent) -> None:
        """
        Handles `Application.KeyEvent` on a key press by performing the corresponding functions.
        If the key pressed is part of the movement keys map `Dict[KeyEvent.key, Bool]`, then the
        key will be set to False for the next `self.move_and_look()` to update the current actions.
        """

        key = event.key
        pressed = Application.KeyEvent.Key
        mod = Application.InputEvent.Modifier

        if key == pressed.F:
            if event.modifiers == mod.SHIFT:
                self.remove_selector_obj()
                self.fm_demo.hide_model()
                logger.info("Command: hide model")
            else:
                logger.info("Command: load model")
                self.fm_demo.load_model()

        elif key == pressed.K:
            # Toggle Key Frames
            self.fm_demo.cycle_model_previews()

        elif key == pressed.SLASH:
            # Toggle reverse direction of motion
            self.fm_demo.is_reversed = not self.fm_demo.is_reversed

        elif key == pressed.M:
            # cycle through mouse modes
            if self.mouse_interaction == MouseMode.MOTION:
                self.remove_selector_obj()
            self.cycle_mouse_mode()
            logger.info(f"Command: mouse mode set to {self.mouse_interaction}")
            return

        elif key == pressed.R:
            self.remove_selector_obj()
            super().reconfigure_sim()
            self.fm_demo = FairmotionInterface(self, metadata_name="fm_demo")
            logger.info("Command: simulator re-loaded")
            self.redraw()
            event.accepted = True
            return

        elif key == pressed.SPACE:
            if not self.sim.config.sim_cfg.enable_physics:
                logger.warn("Warning: physics was not enabled during setup")
            else:
                self.simulating = not self.simulating
                logger.info(f"Command: physics simulating set to {self.simulating}")
            if self.simulating:
                self.remove_selector_obj()
            return

        elif key == pressed.PERIOD:
            if self.simulating:
                logger.warn("Warning: physic simulation already running")
            else:
                self.simulate_single_step = True
                logger.info("Command: physics step taken")
                self.remove_selector_obj()
            return

        super().key_press_event(event)

    def mouse_press_event(self, event: Application.MouseEvent) -> None:
        """
        Handles `Application.MouseEvent`. When in GRAB mode, click on
        objects to drag their position. (right-click for fixed constraints).
        When in MOTION mode select Fairmotion characters with left-click,
        place them in a new location with right-click.
        """
        button = Application.MouseEvent.Button
        physics_enabled = self.sim.get_physics_simulation_library()

        # if interactive mode is True -> MOTION MODE
        if self.mouse_interaction == MouseMode.MOTION and physics_enabled:
            render_camera = self.render_camera.render_camera
            ray = render_camera.unproject(self.get_mouse_position(event.position))
            raycast_results = self.sim.cast_ray(ray=ray)

            if raycast_results.has_hits():
                hit_info = raycast_results.hits[0]

                if event.button == button.LEFT:
                    if self.fm_demo.belongs_to(hit_info.object_id):
                        if not self.fm_demo.model:
                            self.fm_demo.load_model()
                        self.simulating = False
                        self.create_selector_obj(self.fm_demo)
                    else:
                        self.remove_selector_obj()

                elif event.button == button.RIGHT and self.selected_mocap_char:
                    point = hit_info.point
                    self.fm_demo.set_transform_offsets(translate_offset=point)
                    self.create_selector_obj(self.fm_demo)
            # end has raycast hit

        super().mouse_press_event(event)

    def mouse_scroll_event(self, event: Application.MouseScrollEvent) -> None:
        """
        Handles `Application.MouseScrollEvent`. When in LOOK mode, enables camera
        zooming (fine-grained zoom using shift). When in GRAB mode, adjusts the depth
        of the grabber's object. (larger depth change rate using shift). When in MOTION
        mode, rotate them about the floor-normal axis with the scroll wheel. (fine-grained
        rotate using shift).
        """
        if self.mouse_interaction == MouseMode.MOTION and self.selected_mocap_char:
            physics_enabled = self.sim.get_physics_simulation_library()

            scroll_mod_val = (
                event.offset.y
                if abs(event.offset.y) > abs(event.offset.x)
                else event.offset.x
            )

            if not scroll_mod_val:
                return

            # use shift to scale action response
            shift_pressed = event.modifiers == Application.InputEvent.Modifier.SHIFT

            if (
                self.mouse_interaction == MouseMode.MOTION
                and physics_enabled
                and self.selected_mocap_char
            ):
                delta = mn.Quaternion.rotation(
                    mn.Deg(scroll_mod_val * (1 if shift_pressed else 20)),
                    mn.Vector3.z_axis(),
                )
                self.fm_demo.set_transform_offsets(
                    rotate_offset=self.fm_demo.rotation_offset * delta
                )
            self.create_selector_obj(self.fm_demo)

        super().mouse_scroll_event(event)

    def cycle_mouse_mode(self):
        """
        Cycles through mouse modes that belong to the MouseMode emun.
        """
        self.mouse_interaction = MouseMode(
            (self.mouse_interaction.value + 1) % len(MouseMode)
        )

    def create_selector_obj(self, mocap_char: FairmotionInterface):
        """
        Creates the selection icon above the given fairmotion character.
        """
        self.remove_selector_obj()

        # selection sphere icon
        obj = mocap_char.rgd_obj_mgr.add_object_by_template_id(self.sphere_template_id)
        obj.collidable = False
        obj.motion_type = phy.MotionType.KINEMATIC
        obj.translation = mocap_char.model.translation + mn.Vector3(0, 1.10, 0)
        self.select_sphere_obj_id = obj.object_id

        # selection origin box
        obj = mocap_char.rgd_obj_mgr.add_object_by_template_id(self.box_template_id)
        obj.collidable = False
        obj.motion_type = phy.MotionType.KINEMATIC
        obj.translation = mocap_char.translation_offset + mn.Vector3(0, 0.8, 0)
        obj.rotation = mocap_char.rotation_offset
        self.select_box_obj_id = obj.object_id

        self.selected_mocap_char = mocap_char

    def remove_selector_obj(self):
        """
        Removes the selection icon from the sim to indicate de-selection.
        """
        manager = self.sim.get_rigid_object_manager()

        # selection sphere icon
        if self.select_sphere_obj_id != -1:
            manager.remove_object_by_id(self.select_sphere_obj_id)
            self.select_sphere_obj_id = -1

        # selection origin box
        if self.select_box_obj_id != -1:
            manager.remove_object_by_id(self.select_box_obj_id)
            self.select_box_obj_id = -1

        self.selected_mocap_char = None

    def print_help_text(self) -> None:
        """
        Print the Key Command help text.
        """
        logger.info(
            """
=========================================================
Welcome to the Habitat-sim Fairmotion Viewer application!
=========================================================
Mouse Functions ('m' to toggle mode):
----------------
In LOOK mode (default):
    LEFT:
        Click and drag to rotate the agent and look up/down.
    WHEEL:
        Modify orthographic camera zoom/perspective camera FOV
        (+ SHIFT): for fine-grained control

In GRAB mode (with 'enable-physics'):
    LEFT:
        Click and drag to pickup and move an object with a point-to-point constraint (e.g. ball joint).
    RIGHT:
        Click and drag to pickup and move an object with a fixed frame constraint.
    WHEEL (with picked object):
        Pull gripped object closer or push it away.

In MOTION mode (with 'enable-physics'):
    LEFT:
        Click a Fairmotion character to set it as selected or clcik anywhere else to deselect.
    RIGHT (With selected Fairmotion character):
        Click anywhere on the scene to translate a selected Fairmotion character to the clicked location.
    WHEEL (with selected Fairmotion character):
        Rotate the orientation of a selected Fairmotion character along an axis normal to the floor of the scene.
        (+ SHIFT): for fine-grained control

Key Commands:
-------------
    esc:        Exit the application.
    'h':        Display this help message.
    'm':        Cycle through mouse mode.

    Agent Controls:
    'wasd':     Move the agent's body forward/backward and left/right.
    'zx':       Move the agent's body up/down.
    arrow keys: Turn the agent's body left/right and camera look up/down.

    Utilities:
    'r':        Reset the simulator with the most recently loaded scene.

    Object Interactions:
    SPACE:      Toggle physics simulation on/off.
    '.':        Take a single simulation step if not simulating continuously.
    'v':        (physics) Invert gravity.

    Fairmotion Interface:
    'f':        Load model with current motion data.
                [shft] Hide model.
    'k':        Toggle key frame preview of loaded motion.
    '/':        Set motion to play in reverse.
=========================================================
"""
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    # optional arguments
    parser.add_argument(
        "--scene",
        default="NONE",
        type=str,
        help='scene/stage file to load (default: "NONE")',
    )
    parser.add_argument(
        "--dataset",
        default="default",
        type=str,
        metavar="DATASET",
        help="dataset configuration file to use (default: default)",
    )
    parser.add_argument(
        "--disable_physics",
        action="store_true",
        help="disable physics simulation (default: False)",
    )
    parser.add_argument(
        "--amass_path",
        type=str,
        help="amass motion file path to load motion from (default: None)",
    )
    parser.add_argument(
        "--metadata_dir",
        type=str,
        help="directory where metadata files should be saved to (default: None)",
    )
    parser

    args = parser.parse_args()

    # Setting up sim_settings
    sim_settings: Dict[str, Any] = default_sim_settings
    sim_settings["scene"] = args.scene
    sim_settings["scene_dataset_config_file"] = args.dataset
    sim_settings["enable_physics"] = not args.disable_physics

    fm_settings: Dict[str, Any] = {}
    fm_settings["amass_path"] = args.amass_path
    fm_settings["metadata_dir"] = args.metadata_dir

    FairmotionSimInteractiveViewer(sim_settings, fm_settings).exec()