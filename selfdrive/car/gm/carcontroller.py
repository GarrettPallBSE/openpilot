from cereal import car
from common.realtime import DT_CTRL
from common.numpy_fast import interp, clip
from selfdrive.config import Conversions as CV
from selfdrive.car import apply_std_steer_torque_limits, create_gas_interceptor_command
from selfdrive.car.gm import gmcan
from selfdrive.car.gm.values import DBC, NO_ASCM, CanBus, CarControllerParams, CAR
from opendbc.can.packer import CANPacker

VisualAlert = car.CarControl.HUDControl.VisualAlert
GearShifter = car.CarState.GearShifter


class CarController():
  def __init__(self, dbc_name, CP, VM):
    self.start_time = 0.
    self.apply_steer_last = 0
    self.apply_gas = 0
    self.apply_brake = 0

    self.lka_steering_cmd_counter_last = -1
    self.lka_icon_status_last = (False, False)
    self.steer_rate_limited = False

    self.params = CarControllerParams(CP)

    self.packer_pt = CANPacker(DBC[CP.carFingerprint]['pt'])
    self.packer_obj = CANPacker(DBC[CP.carFingerprint]['radar'])
    self.packer_ch = CANPacker(DBC[CP.carFingerprint]['chassis'])

  def update(self, c, enabled, CS, frame, actuators,
             hud_v_cruise, hud_show_lanes, hud_show_car, hud_alert):

    P = self.params

    # Send CAN commands.
    can_sends = []

    # Steering (50Hz)
    # Avoid GM EPS faults when transmitting messages too close together: skip this transmit if we just received the
    # next Panda loopback confirmation in the current CS frame.
    if CS.lka_steering_cmd_counter != self.lka_steering_cmd_counter_last:
      self.lka_steering_cmd_counter_last = CS.lka_steering_cmd_counter
    elif (frame % P.STEER_STEP) == 0:
      lkas_enabled = c.active and not (CS.out.steerFaultTemporary or CS.out.steerFaultPermanent) and CS.out.vEgo > P.MIN_STEER_SPEED
      if lkas_enabled:
        new_steer = int(round(actuators.steer * P.STEER_MAX))
        apply_steer = apply_std_steer_torque_limits(new_steer, self.apply_steer_last, CS.out.steeringTorque, P)
        self.steer_rate_limited = new_steer != apply_steer
      else:
        apply_steer = 0

      self.apply_steer_last = apply_steer
      # GM EPS faults on any gap in received message counters. To handle transient OP/Panda safety sync issues at the
      # moment of disengaging, increment the counter based on the last message known to pass Panda safety checks.
      idx = (CS.lka_steering_cmd_counter + 1) % 4
      
      can_sends.append(gmcan.create_steering_control(self.packer_pt, CanBus.POWERTRAIN, apply_steer, idx, lkas_enabled))

    # TODO: All three conditions should not be required - really only last two?
    # if CS.CP.carFingerprint not in NO_ASCM and CS.CP.openpilotLongitudinalControl and not CS.CP.pcmCruise:
    #   # Gas/regen and brakes - all at 25Hz
    #   if (frame % 4) == 0:
    #     if not c.active:
    #       # Stock ECU sends max regen when not enabled.
    #       self.apply_gas = P.MAX_ACC_REGEN
    #       self.apply_brake = 0
    #     else:
    #       self.apply_gas = int(round(interp(actuators.accel, P.GAS_LOOKUP_BP, P.GAS_LOOKUP_V)))
    #       self.apply_brake = int(round(interp(actuators.accel, P.BRAKE_LOOKUP_BP, P.BRAKE_LOOKUP_V)))

    #     idx = (frame // 4) % 4
    #     # TODO: Should all instances of "enabled" be replaced with c.active?
    #     at_full_stop = enabled and CS.out.standstill
    #     near_stop = enabled and (CS.out.vEgo < P.NEAR_STOP_BRAKE_PHASE)
    #     if CS.CP.carFingerprint == CAR.BOLT_EUV_NR:
    #       can_sends.append(gmcan.create_friction_brake_command(self.packer_pt, CanBus.POWERTRAIN, self.apply_brake, idx, near_stop, at_full_stop))
    #     else:
    #       can_sends.append(gmcan.create_friction_brake_command(self.packer_ch, CanBus.CHASSIS, self.apply_brake, idx, near_stop, at_full_stop))
    #     can_sends.append(gmcan.create_gas_regen_command(self.packer_pt, CanBus.POWERTRAIN, self.apply_gas, idx, enabled, at_full_stop))

    #   # Send dashboard UI commands (ACC status), 25hz
    #   if (frame % 4) == 0:
    #     send_fcw = hud_alert == VisualAlert.fcw
    #     can_sends.append(gmcan.create_acc_dashboard_command(self.packer_pt, CanBus.POWERTRAIN, enabled, hud_v_cruise * CV.MS_TO_KPH, hud_show_car, send_fcw))

    #   # Radar needs to know current speed and yaw rate (50hz),
    #   # and that ADAS is alive (10hz)
    #   time_and_headlights_step = 10
    #   tt = frame * DT_CTRL

    #   if frame % time_and_headlights_step == 0 and CS.CP.carFingerprint != CAR.BOLT_EUV_NR:
    #     idx = (frame // time_and_headlights_step) % 4
    #     can_sends.append(gmcan.create_adas_time_status(CanBus.OBSTACLE, int((tt - self.start_time) * 60), idx))
    #     can_sends.append(gmcan.create_adas_headlights_status(self.packer_obj, CanBus.OBSTACLE))

    #   speed_and_accelerometer_step = 2
    #   if frame % speed_and_accelerometer_step == 0 and CS.CP.carFingerprint != CAR.BOLT_EUV_NR:
    #     idx = (frame // speed_and_accelerometer_step) % 4
    #     can_sends.append(gmcan.create_adas_steering_status(CanBus.OBSTACLE, idx))
    #     can_sends.append(gmcan.create_adas_accelerometer_speed_status(CanBus.OBSTACLE, CS.out.vEgo, idx))

    #   if frame % P.ADAS_KEEPALIVE_STEP == 0:
    #     can_sends += gmcan.create_adas_keepalive(CanBus.POWERTRAIN)
    # elif CS.CP.openpilotLongitudinalControl:
    #   # Gas/regen and brakes - all at 25Hz
    #   if (frame % 4) == 0:
    #     if not c.active:
    #       # Stock ECU sends max regen when not enabled.
    #       self.apply_gas = P.MAX_ACC_REGEN
    #       self.apply_brake = 0
    #     else:
    #       self.apply_gas = int(round(interp(actuators.accel, P.GAS_LOOKUP_BP, P.GAS_LOOKUP_V)))
    #       self.apply_brake = int(round(interp(actuators.accel, P.BRAKE_LOOKUP_BP, P.BRAKE_LOOKUP_V)))

    #     idx = (frame // 4) % 4

    #     at_full_stop = enabled and CS.out.standstill
    #     # near_stop = enabled and (CS.out.vEgo < P.NEAR_STOP_BRAKE_PHASE)
    #     # VOACC based cars have brakes on PT bus - OP won't be doing VOACC for a while
    #     # can_sends.append(gmcan.create_friction_brake_command(self.packer_pt, CanBus.POWERTRAIN, self.apply_brake, idx, near_stop, at_full_stop))
        
    #     if CS.CP.enableGasInterceptor:
    #       # # TODO: JJS Unsure if low is single pedal mode in any non-electric cars
    #       # singlePedalMode = CS.out.gearShifter == GearShifter.low and CS.CP.carFingerprint in EV_CAR
    #       # # TODO: JJS Detect saturated battery and fallback to D mode (until regen is available
    #       # if singlePedalMode:
    #       #   # In L Mode, Pedal applies regen at a fixed coast-point (TODO: max regen in L mode may be different per car)
    #       #   # This will apply to EVs in L mode.
    #       #   # accel values below zero down to a cutoff point 
    #       #   #  that approximates the percentage of braking regen can handle should be scaled between 0 and the coast-point
    #       #   # accell values below this point will need to be add-on future hijacked AEB
    #       #   # TODO: Determine (or guess) at regen precentage

    #       #   # From Felger's Bolt Bort
    #       #   #It seems in L mode, accel / decel point is around 1/5
    #       #   #-1-------AEB------0----regen---0.15-------accel----------+1
    #       #   # Shrink gas request to 0.85, have it start at 0.2
    #       #   # Shrink brake request to 0.85, first 0.15 gives regen, rest gives AEB
            
    #       #   zero = 40/256
            
    #       #   if (actuators.accel > 0.):
    #       #     pedal_gas = clip(((1-zero) * actuators.accel + zero), 0., 1.)
    #       #   else:
    #       #     pedal_gas = clip(actuators.accel, 0., zero) # Make brake the same size as gas, but clip to regen
    #       #     # aeb = actuators.brake*(1-zero)-regen # For use later, braking more than regen
    #       # else:
    #       #   # In D Mode, Pedal is close to coast at 0, 100% at 1.
    #       #   # This will apply to non-EVs and EVs in D mode.
    #       #   # accel values below zero will need to be handled by future hijacked AEB
    #       #   # TODO: Determine if this clipping is correct
    #       #   pedal_gas = clip(actuators.accel, 0., 1.)
    #       #TODO: Add alert when not in L mode
    #       pedal_gas = clip(actuators.accel, 0., 1.)
    #       can_sends.append(create_gas_interceptor_command(self.packer_pt, pedal_gas, idx))
    #     else:
    #       can_sends.append(gmcan.create_gas_regen_command(self.packer_pt, CanBus.POWERTRAIN, self.apply_gas, idx, enabled, at_full_stop))

              
    # Show green icon when LKA torque is applied, and
    # alarming orange icon when approaching torque limit.
    # If not sent again, LKA icon disappears in about 5 seconds.
    # Conveniently, sending camera message periodically also works as a keepalive.
    # lka_active = CS.lkas_status == 1
    # lka_critical = lka_active and abs(actuators.steer) > 0.9
    # lka_icon_status = (lka_active, lka_critical)
    # if CS.CP.carFingerprint != CAR.BOLT_EUV_NR and frame % P.CAMERA_KEEPALIVE_STEP == 0 or lka_icon_status != self.lka_icon_status_last:
    #   steer_alert = hud_alert in (VisualAlert.steerRequired, VisualAlert.ldw)
    #   can_sends.append(gmcan.create_lka_icon_command(CanBus.SW_GMLAN, lka_active, lka_critical, steer_alert))
    #   self.lka_icon_status_last = lka_icon_status

    new_actuators = actuators.copy()
    new_actuators.steer = self.apply_steer_last / P.STEER_MAX
    new_actuators.gas = self.apply_gas
    new_actuators.brake = self.apply_brake

    return new_actuators, can_sends
