"""Config flow for Adaptive Lighting integration."""

from __future__ import annotations

import copy
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any, Callable, Coroutine, cast

import voluptuous as vol
from homeassistant.components.random.config_flow import validate_user_input
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.utility_meter import CONF_METER_TYPE, METER_TYPES
from homeassistant.config_entries import ConfigEntry, ConfigEntryBaseFlow, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_DEVICE,
    CONF_DOMAIN,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
    Platform,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.schema_config_entry_flow import SchemaCommonFlowHandler, SchemaFlowError, SchemaFlowFormStep
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .common import SourceEntity, create_source_entity
from .const import (
    CONF_AREA,
    CONF_AUTOSTART,
    CONF_CALCULATION_ENABLED_CONDITION,
    CONF_CALIBRATE,
    CONF_CREATE_ENERGY_SENSOR,
    CONF_CREATE_ENERGY_SENSORS,
    CONF_CREATE_UTILITY_METERS,
    CONF_DAILY_FIXED_ENERGY,
    CONF_DISABLE_EXTENDED_ATTRIBUTES,
    CONF_DISABLE_LIBRARY_DOWNLOAD,
    CONF_ENERGY_INTEGRATION_METHOD,
    CONF_ENERGY_SENSOR_CATEGORY,
    CONF_ENERGY_SENSOR_FRIENDLY_NAMING,
    CONF_ENERGY_SENSOR_NAMING,
    CONF_ENERGY_SENSOR_PRECISION,
    CONF_ENERGY_SENSOR_UNIT_PREFIX,
    CONF_EXCLUDE_ENTITIES,
    CONF_FIXED,
    CONF_FORCE_CALCULATE_GROUP_ENERGY,
    CONF_FORCE_UPDATE_FREQUENCY,
    CONF_GAMMA_CURVE,
    CONF_GROUP,
    CONF_GROUP_ENERGY_ENTITIES,
    CONF_GROUP_MEMBER_SENSORS,
    CONF_GROUP_POWER_ENTITIES,
    CONF_GROUP_TYPE,
    CONF_HIDE_MEMBERS,
    CONF_IGNORE_UNAVAILABLE_STATE,
    CONF_INCLUDE_NON_POWERCALC_SENSORS,
    CONF_LINEAR,
    CONF_MANUFACTURER,
    CONF_MAX_POWER,
    CONF_MIN_POWER,
    CONF_MODE,
    CONF_MODEL,
    CONF_MULTI_SWITCH,
    CONF_MULTIPLY_FACTOR,
    CONF_MULTIPLY_FACTOR_STANDBY,
    CONF_ON_TIME,
    CONF_PLAYBOOK,
    CONF_PLAYBOOKS,
    CONF_POWER,
    CONF_POWER_OFF,
    CONF_POWER_SENSOR_CATEGORY,
    CONF_POWER_SENSOR_FRIENDLY_NAMING,
    CONF_POWER_SENSOR_NAMING,
    CONF_POWER_SENSOR_PRECISION,
    CONF_POWER_TEMPLATE,
    CONF_REPEAT,
    CONF_SELF_USAGE_INCLUDED,
    CONF_SENSOR_TYPE,
    CONF_SENSORS,
    CONF_STANDBY_POWER,
    CONF_STATE_TRIGGER,
    CONF_STATES_POWER,
    CONF_SUB_GROUPS,
    CONF_SUB_PROFILE,
    CONF_SUBTRACT_ENTITIES,
    CONF_UNAVAILABLE_POWER,
    CONF_UPDATE_FREQUENCY,
    CONF_UTILITY_METER_NET_CONSUMPTION,
    CONF_UTILITY_METER_OFFSET,
    CONF_UTILITY_METER_TARIFFS,
    CONF_UTILITY_METER_TYPES,
    CONF_VALUE,
    CONF_VALUE_TEMPLATE,
    CONF_WLED,
    DISCOVERY_POWER_PROFILES,
    DISCOVERY_SOURCE_ENTITY,
    DOMAIN,
    DOMAIN_CONFIG,
    DUMMY_ENTITY_ID,
    ENERGY_INTEGRATION_METHOD_LEFT,
    ENERGY_INTEGRATION_METHODS,
    ENTITY_CATEGORIES,
    ENTRY_GLOBAL_CONFIG_UNIQUE_ID,
    CalculationStrategy,
    GroupType,
    SensorType,
    UnitPrefix,
)
from .discovery import get_power_profile_by_source_entity
from .errors import ModelNotSupportedError, StrategyConfigurationError
from .power_profile.factory import get_power_profile
from .power_profile.library import ModelInfo, ProfileLibrary
from .power_profile.power_profile import DEVICE_TYPE_DOMAIN, DeviceType, PowerProfile
from .sensors.daily_energy import DEFAULT_DAILY_UPDATE_FREQUENCY
from .strategy.factory import PowerCalculatorStrategyFactory
from .strategy.wled import CONFIG_SCHEMA as SCHEMA_POWER_WLED

_LOGGER = logging.getLogger(__name__)

CONF_CONFIRM_AUTODISCOVERED_MODEL = "confirm_autodisovered_model"


class Steps(StrEnum):
    ADVANCED_OPTIONS = "advanced_options"
    BASIC_OPTIONS = "basic_options"
    DOMAIN_GROUP = "domain_group"
    GROUP = "group"
    LIBRARY = "library"
    LIBRARY_MULTI_PROFILE = "library_multi_profile"
    LIBRARY_OPTIONS = "library_options"
    VIRTUAL_POWER = "virtual_power"
    FIXED = "fixed"
    LINEAR = "linear"
    MULTI_SWITCH = "multi_switch"
    PLAYBOOK = "playbook"
    WLED = "wled"
    POWER_ADVANCED = "power_advanced"
    DAILY_ENERGY = "daily_energy"
    REAL_POWER = "real_power"
    MANUFACTURER = "manufacturer"
    MENU_LIBRARY = "menu_library"
    MENU_GROUP = "menu_group"
    MODEL = "model"
    SUB_PROFILE = "sub_profile"
    USER = "user"
    SMART_SWITCH = "smart_switch"
    SUBTRACT_GROUP = "subtract_group"
    INIT = "init"
    UTILITY_METER_OPTIONS = "utility_meter_options"
    GLOBAL_CONFIGURATION = "global_configuration"
    GLOBAL_CONFIGURATION_ENERGY = "global_configuration_energy"
    GLOBAL_CONFIGURATION_UTILITY_METER = "global_configuration_utility_meter"


MENU_SENSOR_TYPE = {
    Steps.VIRTUAL_POWER: "Virtual power (manual)",
    Steps.MENU_LIBRARY: "Virtual power (library)",
    Steps.MENU_GROUP: "Group",
    Steps.DAILY_ENERGY: "Daily energy",
    Steps.REAL_POWER: "Energy from real power sensor",
}

MENU_GROUP = {
    Steps.GROUP: "Standard group",
    Steps.DOMAIN_GROUP: "Domain based group",
    Steps.SUBTRACT_GROUP: "Subtract group",
}

MENU_OPTIONS = {
    Steps.FIXED: "Fixed options",
    Steps.LINEAR: "Linear options",
    Steps.MULTI_SWITCH: "Multi switch options",
    Steps.PLAYBOOK: "Playbook options",
    Steps.WLED: "WLED options",
}

LIBRARY_URL = "https://library.powercalc.nl"

STRATEGY_STEP_MAPPING = {
    CalculationStrategy.FIXED: Steps.FIXED,
    CalculationStrategy.LINEAR: Steps.LINEAR,
    CalculationStrategy.MULTI_SWITCH: Steps.MULTI_SWITCH,
    CalculationStrategy.PLAYBOOK: Steps.PLAYBOOK,
    CalculationStrategy.WLED: Steps.WLED,
}

SCHEMA_UTILITY_METER_TOGGLE = vol.Schema(
    {
        vol.Optional(CONF_CREATE_UTILITY_METERS, default=False): selector.BooleanSelector(),
    },
)

SCHEMA_ENERGY_SENSOR_TOGGLE = vol.Schema(
    {
        vol.Optional(CONF_CREATE_ENERGY_SENSOR, default=True): selector.BooleanSelector(),
    },
)

SCHEMA_ENERGY_OPTIONS = vol.Schema(
    {
        vol.Optional(
            CONF_ENERGY_INTEGRATION_METHOD,
            default=ENERGY_INTEGRATION_METHOD_LEFT,
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=ENERGY_INTEGRATION_METHODS,
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        ),
        vol.Optional(CONF_ENERGY_SENSOR_UNIT_PREFIX): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=UnitPrefix.KILO, label="k (kilo)"),
                    selector.SelectOptionDict(value=UnitPrefix.MEGA, label="M (mega)"),
                    selector.SelectOptionDict(value=UnitPrefix.GIGA, label="G (giga)"),
                    selector.SelectOptionDict(value=UnitPrefix.TERA, label="T (tera)"),
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        ),
    },
)

SCHEMA_DAILY_ENERGY_OPTIONS = vol.Schema(
    {
        vol.Optional(CONF_VALUE): vol.Coerce(float),
        vol.Optional(CONF_VALUE_TEMPLATE): selector.TemplateSelector(),
        vol.Optional(
            CONF_UNIT_OF_MEASUREMENT,
            default=UnitOfEnergy.KILO_WATT_HOUR,
        ): vol.In(
            [UnitOfEnergy.KILO_WATT_HOUR, UnitOfPower.WATT],
        ),
        vol.Optional(CONF_ON_TIME): selector.DurationSelector(
            selector.DurationSelectorConfig(enable_day=False),
        ),
        vol.Optional(
            CONF_UPDATE_FREQUENCY,
            default=DEFAULT_DAILY_UPDATE_FREQUENCY,
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=10,
                unit_of_measurement=UnitOfTime.SECONDS,
                mode=selector.NumberSelectorMode.BOX,
            ),
        ),
    },
)
SCHEMA_DAILY_ENERGY = vol.Schema(
    {
        vol.Required(CONF_NAME): selector.TextSelector(),
        **SCHEMA_UTILITY_METER_TOGGLE.schema,
    },
).extend(SCHEMA_DAILY_ENERGY_OPTIONS.schema)

SCHEMA_REAL_POWER_OPTIONS = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(device_class=SensorDeviceClass.POWER),
        ),
        vol.Optional(CONF_DEVICE): selector.DeviceSelector(),
    },
)

SCHEMA_REAL_POWER = vol.Schema(
    {
        vol.Required(CONF_NAME): selector.TextSelector(),
        **SCHEMA_REAL_POWER_OPTIONS.schema,
        **SCHEMA_UTILITY_METER_TOGGLE.schema,
    },
).extend(SCHEMA_REAL_POWER_OPTIONS.schema)

SCHEMA_POWER_LIBRARY = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): selector.EntitySelector(),
        vol.Optional(CONF_NAME): selector.TextSelector(),
    },
)

SCHEMA_POWER_OPTIONS = vol.Schema(
    {
        vol.Optional(CONF_STANDBY_POWER): vol.Coerce(float),
        **SCHEMA_ENERGY_SENSOR_TOGGLE.schema,
        **SCHEMA_UTILITY_METER_TOGGLE.schema,
    },
)

SCHEMA_POWER_OPTIONS_LIBRARY = vol.Schema(
    {
        **SCHEMA_ENERGY_SENSOR_TOGGLE.schema,
        **SCHEMA_UTILITY_METER_TOGGLE.schema,
    },
)

SCHEMA_POWER_BASE = vol.Schema(
    {
        vol.Optional(CONF_NAME): selector.TextSelector(),
    },
)

STRATEGY_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            CalculationStrategy.FIXED,
            CalculationStrategy.LINEAR,
            CalculationStrategy.MULTI_SWITCH,
            CalculationStrategy.PLAYBOOK,
            CalculationStrategy.WLED,
            CalculationStrategy.LUT,
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    ),
)

SCHEMA_POWER_FIXED = vol.Schema(
    {
        vol.Optional(CONF_POWER): vol.Coerce(float),
        vol.Optional(CONF_POWER_TEMPLATE): selector.TemplateSelector(),
        vol.Optional(CONF_STATES_POWER): selector.ObjectSelector(),
    },
)

SCHEMA_POWER_SMART_SWITCH = vol.Schema(
    {
        vol.Optional(CONF_POWER): vol.Coerce(float),
        vol.Optional(CONF_SELF_USAGE_INCLUDED): selector.BooleanSelector(),
    },
)

SCHEMA_POWER_LINEAR = vol.Schema(
    {
        vol.Optional(CONF_MIN_POWER): vol.Coerce(float),
        vol.Optional(CONF_MAX_POWER): vol.Coerce(float),
        vol.Optional(CONF_GAMMA_CURVE): vol.Coerce(float),
        vol.Optional(CONF_CALIBRATE): selector.ObjectSelector(),
    },
)

SCHEMA_POWER_MULTI_SWITCH = vol.Schema(
    {
        vol.Required(CONF_ENTITIES): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=Platform.SWITCH, multiple=True),
        ),
    },
)
SCHEMA_POWER_MULTI_SWITCH_MANUAL = vol.Schema(
    {
        **SCHEMA_POWER_MULTI_SWITCH.schema,
        vol.Required(CONF_POWER): vol.Coerce(float),
        vol.Required(CONF_POWER_OFF): vol.Coerce(float),
    },
)

SCHEMA_POWER_PLAYBOOK = vol.Schema(
    {
        vol.Optional(CONF_PLAYBOOKS): selector.ObjectSelector(),
        vol.Optional(CONF_REPEAT): selector.BooleanSelector(),
        vol.Optional(CONF_AUTOSTART): selector.TextSelector(),
        vol.Optional(CONF_STATE_TRIGGER): selector.ObjectSelector(),
    },
)

SCHEMA_POWER_AUTODISCOVERED = vol.Schema(
    {vol.Optional(CONF_CONFIRM_AUTODISCOVERED_MODEL, default=True): bool},
)

SCHEMA_POWER_ADVANCED = vol.Schema(
    {
        vol.Optional(CONF_CALCULATION_ENABLED_CONDITION): selector.TemplateSelector(),
        vol.Optional(CONF_IGNORE_UNAVAILABLE_STATE): selector.BooleanSelector(),
        vol.Optional(CONF_UNAVAILABLE_POWER): vol.Coerce(float),
        vol.Optional(CONF_MULTIPLY_FACTOR): vol.Coerce(float),
        vol.Optional(CONF_MULTIPLY_FACTOR_STANDBY): selector.BooleanSelector(),
    },
)

SCHEMA_GROUP = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Optional(CONF_UNIQUE_ID): selector.TextSelector(),
        vol.Optional(CONF_DEVICE): selector.DeviceSelector(),
    },
)

SCHEMA_GROUP_DOMAIN = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_DOMAIN): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["all"] + [cls.value for cls in Platform],
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        ),
        vol.Optional(CONF_EXCLUDE_ENTITIES): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=Platform.SENSOR,
                device_class=[SensorDeviceClass.ENERGY, SensorDeviceClass.POWER],
                multiple=True,
            ),
        ),
        **SCHEMA_ENERGY_SENSOR_TOGGLE.schema,
        **SCHEMA_UTILITY_METER_TOGGLE.schema,
    },
)

SCHEMA_GROUP_SUBTRACT_OPTIONS = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=Platform.SENSOR,
                device_class=SensorDeviceClass.POWER,
                multiple=False,
            ),
        ),
        vol.Optional(CONF_SUBTRACT_ENTITIES): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=Platform.SENSOR,
                device_class=SensorDeviceClass.POWER,
                multiple=True,
            ),
        ),
    },
)

SCHEMA_GROUP_SUBTRACT = vol.Schema(
    {
        vol.Required(CONF_NAME): selector.TextSelector(),
        vol.Optional(CONF_UNIQUE_ID): selector.TextSelector(),
        **SCHEMA_GROUP_SUBTRACT_OPTIONS.schema,
        **SCHEMA_ENERGY_SENSOR_TOGGLE.schema,
        **SCHEMA_UTILITY_METER_TOGGLE.schema,
    },
)

SCHEMA_UTILITY_METER_OPTIONS = vol.Schema(
    {
        vol.Required(CONF_UTILITY_METER_TYPES): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=METER_TYPES,
                translation_key=CONF_METER_TYPE,
                multiple=True,
            ),
        ),
        vol.Optional(CONF_UTILITY_METER_TARIFFS, default=[]): selector.SelectSelector(
            selector.SelectSelectorConfig(options=[], custom_value=True, multiple=True),
        ),
        vol.Optional(CONF_UTILITY_METER_NET_CONSUMPTION, default=False): selector.BooleanSelector(),
        vol.Required(CONF_UTILITY_METER_OFFSET, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=28,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="days",
            ),
        ),
    },
)

SCHEMA_GLOBAL_CONFIGURATION = vol.Schema(
    {
        vol.Optional(CONF_POWER_SENSOR_NAMING): selector.TextSelector(),
        vol.Optional(CONF_POWER_SENSOR_FRIENDLY_NAMING): selector.TextSelector(),
        vol.Optional(CONF_POWER_SENSOR_CATEGORY): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(filter(lambda item: item is not None, ENTITY_CATEGORIES)),  # type: ignore
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        ),
        vol.Optional(CONF_POWER_SENSOR_PRECISION): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=6, mode=selector.NumberSelectorMode.BOX, step=1),
        ),
        vol.Optional(CONF_FORCE_UPDATE_FREQUENCY): selector.NumberSelector(
            selector.NumberSelectorConfig(unit_of_measurement=UnitOfTime.SECONDS, mode=selector.NumberSelectorMode.BOX),
        ),
        vol.Optional(CONF_IGNORE_UNAVAILABLE_STATE, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_INCLUDE_NON_POWERCALC_SENSORS, default=True): selector.BooleanSelector(),
        vol.Optional(CONF_DISABLE_EXTENDED_ATTRIBUTES, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_DISABLE_LIBRARY_DOWNLOAD, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_CREATE_ENERGY_SENSORS, default=True): selector.BooleanSelector(),
        **SCHEMA_UTILITY_METER_TOGGLE.schema,
    },
)

SCHEMA_GLOBAL_CONFIGURATION_ENERGY_SENSOR = vol.Schema(
    {
        vol.Optional(CONF_ENERGY_SENSOR_NAMING): selector.TextSelector(),
        vol.Optional(CONF_ENERGY_SENSOR_FRIENDLY_NAMING): selector.TextSelector(),
        vol.Optional(CONF_ENERGY_SENSOR_CATEGORY): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(filter(lambda item: item is not None, ENTITY_CATEGORIES)),  # type: ignore
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        ),
        **SCHEMA_ENERGY_OPTIONS.schema,
        vol.Optional(CONF_ENERGY_SENSOR_PRECISION): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=6, mode=selector.NumberSelectorMode.BOX, step=1),
        ),
    },
)

@dataclass(slots=True)
class PowercalcFormStep(SchemaFlowFormStep):
    step: Steps = None

    extra_form_args: dict[str, Any] = None


class PowercalcCommonFlow(ABC, ConfigEntryBaseFlow, SchemaCommonFlowHandler):

    def __init__(self) -> None:
        """Initialize options flow."""
        self.sensor_config: ConfigType = {}
        self.global_config: ConfigType = {}
        self.source_entity: SourceEntity | None = None
        self.source_entity_id: str | None = None
        self.selected_profile: PowerProfile | None = None
        self.is_library_flow: bool = False
        self.skip_advanced_step: bool = False
        self.is_options_flow: bool = isinstance(self, OptionsFlow)

    @abstractmethod
    @callback
    def persist_config_entry(self) -> FlowResult:
        pass  # pragma: no cover

    async def validate_strategy_config(self, user_input: dict[str, Any] | None = None) -> None:
        """Validate the strategy config."""
        strategy_name = CalculationStrategy(
            self.sensor_config.get(CONF_MODE) or self.selected_profile.calculation_strategy,  # type: ignore
        )
        factory = PowerCalculatorStrategyFactory(self.hass)
        strategy = await factory.create(user_input or self.sensor_config, strategy_name, self.selected_profile, self.source_entity)  # type: ignore
        try:
            await strategy.validate_config()
        except StrategyConfigurationError as error:
            translation = error.get_config_flow_translate_key()
            if translation is None:
                translation = "unknown"
            _LOGGER.error(str(error))
            raise SchemaFlowError(translation)
            #return {"base": translation}
        #return {}

    @staticmethod
    def validate_group_input(user_input: dict[str, Any] | None = None) -> dict:
        """Validate the group form."""
        if not user_input:
            return {}
        errors: dict[str, str] = {}

        if (
            CONF_SUB_GROUPS not in user_input
            and CONF_GROUP_POWER_ENTITIES not in user_input
            and CONF_GROUP_ENERGY_ENTITIES not in user_input
            and CONF_GROUP_MEMBER_SENSORS not in user_input
            and CONF_AREA not in user_input
        ):
            errors["base"] = "group_mandatory"

        return errors

    @staticmethod
    def validate_daily_energy_input(user_input: dict[str, Any] | None) -> dict:
        """Validates the daily energy form."""
        if not user_input:
            return {}
        errors: dict[str, str] = {}

        if CONF_VALUE not in user_input and CONF_VALUE_TEMPLATE not in user_input:
            errors["base"] = "daily_energy_mandatory"

        return errors

    def create_strategy_schema(self, strategy: CalculationStrategy, source_entity_id: str) -> vol.Schema:
        """Get the config schema for a given power calculation strategy."""
        if strategy == CalculationStrategy.LINEAR:
            return self.create_schema_linear(source_entity_id)
        if strategy == CalculationStrategy.PLAYBOOK:
            return SCHEMA_POWER_PLAYBOOK
        if strategy == CalculationStrategy.MULTI_SWITCH:
            return self.create_schema_multi_switch()
        if strategy == CalculationStrategy.WLED:
            return SCHEMA_POWER_WLED
        return SCHEMA_POWER_FIXED

    def create_daily_energy_schema(self) -> vol.Schema:
        """Create the config schema for daily energy sensor."""
        return SCHEMA_DAILY_ENERGY.extend(  # type: ignore
            {
                vol.Optional(CONF_GROUP): self.create_group_selector(),
            },
        )

    def create_schema_group(
        self,
        config_entry: ConfigEntry | None = None,
        is_option_flow: bool = False,
    ) -> vol.Schema:
        """Create config schema for groups."""
        member_sensors = [
            selector.SelectOptionDict(value=config_entry.entry_id, label=config_entry.title)
            for config_entry in self.hass.config_entries.async_entries(DOMAIN)
            if config_entry.data.get(CONF_SENSOR_TYPE) in [SensorType.VIRTUAL_POWER, SensorType.REAL_POWER]
            and config_entry.unique_id is not None
            and config_entry.title is not None
        ]
        member_sensor_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=member_sensors,
                multiple=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        )

        schema = vol.Schema(
            {
                vol.Optional(CONF_GROUP_MEMBER_SENSORS): member_sensor_selector,
                vol.Optional(CONF_GROUP_POWER_ENTITIES): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=Platform.SENSOR,
                        device_class=SensorDeviceClass.POWER,
                        multiple=True,
                    ),
                ),
                vol.Optional(CONF_GROUP_ENERGY_ENTITIES): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=Platform.SENSOR,
                        device_class=SensorDeviceClass.ENERGY,
                        multiple=True,
                    ),
                ),
                vol.Optional(CONF_SUB_GROUPS): self.create_group_selector(
                    current_entry=config_entry,
                    multiple=True,
                ),
                vol.Optional(CONF_AREA): selector.AreaSelector(),
                vol.Optional(CONF_DEVICE): selector.DeviceSelector(),
                vol.Optional(CONF_HIDE_MEMBERS, default=False): selector.BooleanSelector(),
                vol.Optional(CONF_INCLUDE_NON_POWERCALC_SENSORS, default=True): selector.BooleanSelector(),
                vol.Optional(CONF_FORCE_CALCULATE_GROUP_ENERGY, default=False): selector.BooleanSelector(),
            },
        )

        if not is_option_flow:
            schema = schema.extend(
                {
                    **SCHEMA_ENERGY_SENSOR_TOGGLE.schema,
                    **SCHEMA_UTILITY_METER_TOGGLE.schema,
                },
            )

        return schema

    @staticmethod
    def create_schema_linear(source_entity_id: str) -> vol.Schema:
        """Create the config schema for linear strategy."""
        return SCHEMA_POWER_LINEAR.extend(  # type: ignore
            {
                vol.Optional(CONF_ATTRIBUTE): selector.AttributeSelector(
                    selector.AttributeSelectorConfig(
                        entity_id=source_entity_id,
                        hide_attributes=[],
                    ),
                ),
            },
        )

    def create_schema_multi_switch(self) -> vol.Schema:
        """Create the config schema for multi switch strategy."""
        return SCHEMA_POWER_MULTI_SWITCH if self.is_library_flow else SCHEMA_POWER_MULTI_SWITCH_MANUAL

    def create_schema_virtual_power(
        self,
    ) -> vol.Schema:
        """Create the config schema for virtual power sensor."""
        schema = vol.Schema(
            {
                vol.Optional(CONF_ENTITY_ID): self.create_source_entity_selector(),
            },
        ).extend(SCHEMA_POWER_BASE.schema)
        schema = schema.extend({vol.Optional(CONF_GROUP): self.create_group_selector()})
        if not self.is_library_flow:
            schema = schema.extend(
                {
                    vol.Optional(
                        CONF_MODE,
                        default=CalculationStrategy.FIXED,
                    ): STRATEGY_SELECTOR,
                },
            )
            options_schema = SCHEMA_POWER_OPTIONS
        else:
            options_schema = SCHEMA_POWER_OPTIONS_LIBRARY

        power_options = self.fill_schema_defaults(
            options_schema,
            self.get_global_powercalc_config(),
        )
        return schema.extend(power_options.schema)  # type: ignore

    async def create_schema_manufacturer(self) -> vol.Schema:
        """Create manufacturer schema."""
        library = await ProfileLibrary.factory(self.hass)
        manufacturers = [
            selector.SelectOptionDict(value=manufacturer, label=manufacturer)
            for manufacturer in await library.get_manufacturer_listing(self.source_entity.domain)  # type: ignore
        ]
        return vol.Schema(
            {
                vol.Required(CONF_MANUFACTURER, default=self.sensor_config.get(CONF_MANUFACTURER)): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=manufacturers,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    ),
                ),
            },
        )

    async def create_schema_model(self) -> vol.Schema:
        """Create model schema."""
        manufacturer = str(self.sensor_config.get(CONF_MANUFACTURER))
        library = await ProfileLibrary.factory(self.hass)
        models = [
            selector.SelectOptionDict(value=model, label=model)
            for model in await library.get_model_listing(manufacturer, self.source_entity.domain)  # type: ignore
        ]
        return vol.Schema(
            {
                vol.Required(CONF_MODEL, default=self.sensor_config.get(CONF_MODEL)): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=models,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    ),
                ),
            },
        )

    async def create_schema_sub_profile(
        self,
        model_info: ModelInfo,
    ) -> vol.Schema:
        """Create sub profile schema."""
        library = await ProfileLibrary.factory(self.hass)
        profile = await library.get_profile(model_info)
        sub_profiles = [selector.SelectOptionDict(value=sub_profile, label=sub_profile) for sub_profile in await profile.get_sub_profiles()]
        return vol.Schema(
            {
                vol.Required(CONF_SUB_PROFILE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=sub_profiles,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    ),
                ),
            },
        )

    def create_schema_advanced(self) -> vol.Schema:
        """Create the advanced options schema."""
        schema = SCHEMA_POWER_ADVANCED

        if self.sensor_config.get(CONF_CREATE_ENERGY_SENSOR):
            schema = schema.extend(SCHEMA_ENERGY_OPTIONS.schema)

        return schema

    def create_source_entity_selector(
        self,
    ) -> selector.EntitySelector:
        """Create the entity selector for the source entity."""
        if self.is_library_flow:
            return selector.EntitySelector(
                selector.EntitySelectorConfig(domain=list(DEVICE_TYPE_DOMAIN.values())),
            )
        return selector.EntitySelector()

    def create_group_selector(
        self,
        current_entry: ConfigEntry | None = None,
        multiple: bool = False,
    ) -> selector.SelectSelector:
        """Create the group selector."""
        options = [
            selector.SelectOptionDict(
                value=config_entry.entry_id,
                label=str(config_entry.data.get(CONF_NAME)),
            )
            for config_entry in self.hass.config_entries.async_entries(DOMAIN)
            if config_entry.data.get(CONF_SENSOR_TYPE) == SensorType.GROUP
            and (current_entry is None or config_entry.entry_id != current_entry.entry_id)
        ]

        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=options,
                multiple=multiple,
                mode=selector.SelectSelectorMode.DROPDOWN,
                custom_value=True,
            ),
        )

    def build_strategy_config(
        self,
        strategy: CalculationStrategy,
        source_entity_id: str,
        user_input: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the config dict needed for the configured strategy."""
        strategy_schema = self.create_strategy_schema(strategy, source_entity_id)
        strategy_options: dict[str, Any] = {}
        for key in strategy_schema.schema:
            if user_input.get(key) is None:
                continue
            strategy_options[str(key)] = user_input.get(key)
        return strategy_options

    @staticmethod
    def build_daily_energy_config(user_input: dict[str, Any], schema: vol.Schema) -> dict[str, Any]:
        """Build the config under daily_energy: key."""
        config: dict[str, Any] = {
            CONF_DAILY_FIXED_ENERGY: {},
        }
        for key, val in user_input.items():
            if key in schema.schema and val is not None:
                if key in {CONF_CREATE_UTILITY_METERS, CONF_GROUP, CONF_NAME, CONF_UNIQUE_ID}:
                    config[str(key)] = val
                    continue

                config[CONF_DAILY_FIXED_ENERGY][str(key)] = val
        return config

    @staticmethod
    def fill_schema_defaults(
        data_schema: vol.Schema,
        options: dict[str, str],
    ) -> vol.Schema:
        """Make a copy of the schema with suggested values set to saved options."""
        schema = {}
        for key, val in data_schema.schema.items():
            new_key = key
            if key in options and isinstance(key, vol.Marker):
                if isinstance(key, vol.Optional) and callable(key.default) and key.default():
                    new_key = vol.Optional(key.schema, default=options.get(key))  # type: ignore
                else:
                    new_key = copy.copy(key)
                    new_key.description = {"suggested_value": options.get(key)}  # type: ignore
            schema[new_key] = val
        return vol.Schema(schema)

    def get_global_powercalc_config(self) -> ConfigType:
        """Get the global powercalc config."""
        if self.global_config:
            return self.global_config
        powercalc = self.hass.data.get(DOMAIN) or {}
        global_config = dict.copy(powercalc.get(DOMAIN_CONFIG) or {})
        force_update_frequency = global_config.get(CONF_FORCE_UPDATE_FREQUENCY)
        if isinstance(force_update_frequency, timedelta):
            global_config[CONF_FORCE_UPDATE_FREQUENCY] = force_update_frequency.total_seconds()
        utility_meter_offset = global_config.get(CONF_UTILITY_METER_OFFSET)
        if isinstance(utility_meter_offset, timedelta):
            global_config[CONF_UTILITY_METER_OFFSET] = utility_meter_offset.days
        if CONF_SENSORS in global_config:
            global_config.pop(CONF_SENSORS)
        self.global_config = global_config
        return global_config

    def get_fixed_power_config_for_smart_switch(self, user_input: dict[str, Any]) -> dict[str, Any]:
        """Get the fixed power config for smart switch."""
        if self.selected_profile is None:
            return {CONF_POWER: 0}  # pragma: no cover
        self_usage_on = self.selected_profile.fixed_mode_config.get(CONF_POWER, 0) if self.selected_profile.fixed_mode_config else 0
        power = user_input.get(CONF_POWER, 0)
        self_usage_included = user_input.get(CONF_SELF_USAGE_INCLUDED, True)
        if self_usage_included:
            power += self_usage_on
        return {CONF_POWER: power}

    async def handle_form_step(
        self,
        form_step: PowercalcFormStep,
        user_input: dict[str, Any] | None = None,
    ):
        """Handle the current step."""
        if user_input is not None:
            if form_step.validate_user_input is not None:
                try:
                    user_input = await form_step.validate_user_input(self, user_input)
                except SchemaFlowError as exc:
                    return await self._show_form(form_step, exc)

            self.sensor_config.update(user_input)
            return await getattr(self, f"async_step_{form_step.next_step}")()

        return await self._show_form(form_step)

    async def _show_form(self, form_step: PowercalcFormStep, error: SchemaFlowError | None = None) -> ConfigFlowResult:
        # Show form for next step
        last_step = None
        if not callable(form_step.next_step):
            last_step = form_step.next_step is None

        return self.async_show_form(
            step_id=form_step.step,
            data_schema=form_step.schema,
            errors={"base": str(error)} if error else {},
            last_step=last_step,
        )

    async def async_step_manufacturer(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Ask the user to select the manufacturer."""
        return await self.handle_form_step(
            PowercalcFormStep(
                step=Steps.MANUFACTURER,
                schema=await self.create_schema_manufacturer(),
                next_step=Steps.MODEL,
            ),
            user_input,
        )

    async def async_step_model(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Ask the user to select the model."""

        async def _validate(_, user_input: dict[str, Any]) -> dict[str, str]:
            library = await ProfileLibrary.factory(self.hass)
            profile = await library.get_profile(
                ModelInfo(
                    str(self.sensor_config.get(CONF_MANUFACTURER)),
                    str(user_input.get(CONF_MODEL)),
                ),
            )
            self.selected_profile = profile
            if self.selected_profile and not await self.selected_profile.has_sub_profiles:
                await self.validate_strategy_config()
            return user_input

        return await self.handle_form_step(
            PowercalcFormStep(
                step=Steps.MODEL,
                schema=await self.create_schema_model(),
                next_step="post_library",
                validate_user_input=_validate
            ),
            user_input,
        )


    async def async_step_post_library(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """
        Handles the logic after the user either selected manufacturer/model himself or confirmed autodiscovered.
        Forwards to the next step in the flow.
        """
        if self.selected_profile and await self.selected_profile.has_sub_profiles and not self.selected_profile.sub_profile_select:
            return await self.async_step_sub_profile()

        if self.selected_profile and self.selected_profile.needs_fixed_config:
            return await self.async_step_fixed()

        if self.selected_profile and self.selected_profile.needs_linear_config:
            return await self.async_step_linear()

        if (
            self.selected_profile
            and self.selected_profile.device_type == DeviceType.SMART_SWITCH
            and self.selected_profile.calculation_strategy == CalculationStrategy.FIXED
        ):
            return await self.async_step_smart_switch()

        if self.selected_profile and self.selected_profile.calculation_strategy == CalculationStrategy.MULTI_SWITCH:
            return await self.async_step_multi_switch()

        return await self.async_step_power_advanced()

    async def async_step_sub_profile(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the flow for sub profile selection."""

        async def _validate(_, input: dict[str, Any]) -> dict[str, str]:
            input[CONF_MODEL] = f"{self.sensor_config.get(CONF_MODEL)}/{input.get(CONF_SUB_PROFILE)}"
            del(input[CONF_SUB_PROFILE])
            return user_input

        return await self.handle_form_step(
            PowercalcFormStep(
                step=Steps.SUB_PROFILE,
                schema=await self.create_schema_sub_profile(
                    ModelInfo(
                        str(self.sensor_config.get(CONF_MANUFACTURER)),
                        str(self.sensor_config.get(CONF_MODEL)),
                    ),
                ),
                next_step=Steps.POWER_ADVANCED,
                validate_user_input=_validate,
            ),
            user_input
        )

    async def async_step_power_advanced(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the flow for advanced options."""

        if self.is_options_flow:
            return self.persist_config_entry()

        if user_input is not None or self.skip_advanced_step:
            self.sensor_config.update(user_input or {})
            if self.sensor_config.get(CONF_CREATE_UTILITY_METERS):
                return await self.async_step_utility_meter_options()
            return self.persist_config_entry()

        return self.async_show_form(
            step_id=Steps.POWER_ADVANCED,
            data_schema=self.fill_schema_defaults(
                self.create_schema_advanced(),
                self.get_global_powercalc_config(),
            ),
            errors={},
        )

    async def async_step_smart_switch(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Asks the user for the power of connect appliance for the smart switch."""
        if user_input is not None:
            self.sensor_config.update(
                {
                    CONF_SELF_USAGE_INCLUDED: user_input.get(CONF_SELF_USAGE_INCLUDED),
                    CONF_MODE: CalculationStrategy.FIXED,
                    CONF_FIXED: self.get_fixed_power_config_for_smart_switch(user_input),
                },
            )
            return await self.async_step_power_advanced()

        self_usage_on = 0
        if self.selected_profile and self.selected_profile.fixed_mode_config:
            self_usage_on = self.selected_profile.fixed_mode_config.get(CONF_POWER, 0)
        return self.async_show_form(
            step_id=Steps.SMART_SWITCH,
            data_schema=SCHEMA_POWER_SMART_SWITCH,
            description_placeholders={"self_usage_power": str(self_usage_on)},
            errors={},
            last_step=False,
        )

    async def async_step_fixed(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the flow for fixed sensor."""

        async def validate(_, user_input):
            user_input = {CONF_FIXED: user_input}
            await self.validate_strategy_config(user_input)
            return user_input

        return await self.handle_form_step(
            PowercalcFormStep(
                step=Steps.FIXED,
                schema=SCHEMA_POWER_FIXED,
                next_step=Steps.POWER_ADVANCED,
                validate_user_input=validate,
            ),
            user_input
        )

    async def async_step_linear(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the flow for linear sensor."""

        async def validate(_, user_input):
            user_input = {CONF_LINEAR: user_input}
            await self.validate_strategy_config(user_input)
            return user_input

        return await self.handle_form_step(
            PowercalcFormStep(
                step=Steps.LINEAR,
                schema=self.create_schema_linear(self.source_entity_id),
                next_step=Steps.POWER_ADVANCED,
                validate_user_input=validate,
            ),
            user_input
        )

    async def async_step_multi_switch(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the flow for multi switch strategy."""
        errors = {}
        if user_input is not None:
            self.sensor_config.update({CONF_MULTI_SWITCH: user_input})
            try:
                await self.validate_strategy_config()
            except SchemaFlowError as exc:
                errors["base"] = str(exc)
            if not errors:
                return await self.async_step_power_advanced()

        return self.async_show_form(
            step_id=Steps.MULTI_SWITCH,
            data_schema=self.create_schema_multi_switch(),
            errors=errors,
            last_step=False,
        )

    async def async_step_utility_meter_options(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the flow for utility meter options."""
        if user_input is not None:
            self.sensor_config.update(user_input or {})
            return self.persist_config_entry()

        return self.async_show_form(
            step_id=Steps.UTILITY_METER_OPTIONS,
            data_schema=self.fill_schema_defaults(
                SCHEMA_UTILITY_METER_OPTIONS,
                self.get_global_powercalc_config(),
            ),
            errors={},
        )

    async def async_step_global_configuration_energy(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the global configuration step."""

        if user_input is not None:
            self.global_config.update(user_input)
            if self.is_options_flow:
                return self.persist_config_entry()

        if not bool(self.global_config.get(CONF_CREATE_ENERGY_SENSORS)) or user_input is not None:
            return await self.async_step_global_configuration_utility_meter()

        return self.async_show_form(
            step_id=Steps.GLOBAL_CONFIGURATION_ENERGY,
            data_schema=self.fill_schema_defaults(
                SCHEMA_GLOBAL_CONFIGURATION_ENERGY_SENSOR,
                self.global_config,
            ),
            errors={},
        )

    async def async_step_global_configuration_utility_meter(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the global configuration step."""

        if user_input is not None:
            self.global_config.update(user_input)
            if self.is_options_flow:
                return self.persist_config_entry()

        if not bool(self.global_config.get(CONF_CREATE_UTILITY_METERS)) or user_input is not None:
            return self.async_create_entry(
                title="Global Configuration",
                data=self.global_config,
            )

        return self.async_show_form(
            step_id=Steps.GLOBAL_CONFIGURATION_UTILITY_METER,
            data_schema=self.fill_schema_defaults(
                SCHEMA_UTILITY_METER_OPTIONS,
                self.global_config,
            ),
            errors={},
        )


class PowercalcConfigFlow(PowercalcCommonFlow, ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PowerCalc."""

    VERSION = 4

    def __init__(self) -> None:
        """Initialize options flow."""
        self.selected_sensor_type: str | None = None
        self.discovered_profiles: dict[str, PowerProfile] = {}
        self.name: str | None = None
        super().__init__()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return PowercalcOptionsFlow(config_entry)

    async def async_step_integration_discovery(
        self,
        discovery_info: DiscoveryInfoType,
    ) -> ConfigFlowResult:
        """Handle integration discovery."""
        _LOGGER.debug("Starting discovery flow: %s", discovery_info)

        self.skip_advanced_step = True  # We don't want to ask advanced options when discovered

        await self.async_set_unique_id(discovery_info.get(CONF_UNIQUE_ID, str(uuid.uuid4())))
        self.selected_sensor_type = SensorType.VIRTUAL_POWER
        self.source_entity = discovery_info[DISCOVERY_SOURCE_ENTITY]
        del discovery_info[DISCOVERY_SOURCE_ENTITY]
        if not self.source_entity:
            return self.async_abort(reason="No source entity set")  # pragma: no cover

        self.source_entity_id = self.source_entity.entity_id
        self.name = self.source_entity.name

        power_profiles: list[PowerProfile] = []
        if DISCOVERY_POWER_PROFILES in discovery_info:
            power_profiles = discovery_info[DISCOVERY_POWER_PROFILES]
            self.discovered_profiles = {profile.unique_id: profile for profile in power_profiles}
            if len(power_profiles) == 1:
                self.selected_profile = power_profiles[0]
            del discovery_info[DISCOVERY_POWER_PROFILES]

        self.sensor_config = discovery_info.copy()

        self.context["title_placeholders"] = {
            "name": self.name or "",
            "manufacturer": str(self.sensor_config.get(CONF_MANUFACTURER)),
            "model": str(self.sensor_config.get(CONF_MODEL)),
        }
        self.is_library_flow = True

        if discovery_info.get(CONF_MODE) == CalculationStrategy.WLED:
            return await self.async_step_wled()

        if len(power_profiles) > 1:
            return cast(ConfigFlowResult, await self.async_step_library_multi_profile())

        return cast(ConfigFlowResult, await self.async_step_library())

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial step."""

        menu = MENU_SENSOR_TYPE

        global_config_entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN,
            ENTRY_GLOBAL_CONFIG_UNIQUE_ID,
        )
        if not global_config_entry:
            menu = {Steps.GLOBAL_CONFIGURATION: "Global configuration", **menu}

        await self.async_set_unique_id(str(uuid.uuid4()))

        return self.async_show_menu(step_id=Steps.USER, menu_options=menu)

    async def async_step_menu_library(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the Virtual power (library) step.
        We forward to the virtual_power step, but without the strategy selector displayed.
        """
        self.is_library_flow = True
        return await self.async_step_virtual_power(user_input)

    async def async_step_global_configuration(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the global configuration step."""
        self.global_config = self.get_global_powercalc_config()
        await self.async_set_unique_id(ENTRY_GLOBAL_CONFIG_UNIQUE_ID)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            self.global_config.update(user_input)
            return await self.async_step_global_configuration_energy()

        return self.async_show_form(
            step_id=Steps.GLOBAL_CONFIGURATION,
            data_schema=self.fill_schema_defaults(
                SCHEMA_GLOBAL_CONFIGURATION,
                self.global_config,
            ),
            errors={},
        )

    async def async_step_virtual_power(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the flow for virtual power sensor."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_strategy = CalculationStrategy(
                user_input.get(CONF_MODE) or CalculationStrategy.LUT,
            )
            entity_id = user_input.get(CONF_ENTITY_ID)
            if selected_strategy is not CalculationStrategy.PLAYBOOK and user_input.get(CONF_NAME) is None and entity_id is None:
                errors[CONF_ENTITY_ID] = "entity_mandatory"

            if not errors:
                self.source_entity_id = str(entity_id or DUMMY_ENTITY_ID)
                self.source_entity = await create_source_entity(
                    self.source_entity_id,
                    self.hass,
                )

                self.name = user_input.get(CONF_NAME) or self.source_entity.name
                self.selected_sensor_type = SensorType.VIRTUAL_POWER
                self.sensor_config.update(user_input)

                return await self.forward_to_strategy_step(selected_strategy)

        return self.async_show_form(
            step_id=Steps.VIRTUAL_POWER,
            data_schema=self.create_schema_virtual_power(),
            errors=errors,
            last_step=False,
        )

    async def forward_to_strategy_step(
        self,
        strategy: CalculationStrategy,
    ) -> FlowResult:
        """Forward to the next step based on the selected strategy."""
        step = STRATEGY_STEP_MAPPING.get(strategy)
        if step is None:
            return await self.async_step_library()
        method = getattr(self, f"async_step_{step}")
        return await method()  # type: ignore

    async def async_step_daily_energy(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the flow for daily energy sensor."""
        errors = self.validate_daily_energy_input(user_input)

        schema = self.create_daily_energy_schema()
        if user_input is not None and not errors:
            self.selected_sensor_type = SensorType.DAILY_ENERGY
            self.name = user_input.get(CONF_NAME)

            self.sensor_config.update(self.build_daily_energy_config(user_input, schema))
            if self.sensor_config.get(CONF_CREATE_UTILITY_METERS):
                return await self.async_step_utility_meter_options()
            return self.persist_config_entry()

        return self.async_show_form(
            step_id=Steps.DAILY_ENERGY,
            data_schema=schema,
            errors=errors,
        )

    async def async_step_menu_group(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the group choice step."""
        return self.async_show_menu(step_id=Steps.MENU_GROUP, menu_options=MENU_GROUP)

    async def async_step_group(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the flow for group sensor."""
        errors = self.validate_group_input(user_input)
        if user_input is not None and not errors:
            self.name = user_input.get(CONF_NAME)
            self.sensor_config.update(user_input)
            return await self.async_handle_group_creation()

        group_schema = SCHEMA_GROUP.extend(
            self.create_schema_group().schema,
        )
        return self.async_show_form(
            step_id=Steps.GROUP,
            data_schema=self.fill_schema_defaults(
                group_schema,
                self.get_global_powercalc_config(),
            ),
            errors=errors,
        )

    async def async_step_domain_group(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the flow for domain based group sensor."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self.name = user_input.get(CONF_NAME)
            self.sensor_config.update(user_input)
            self.sensor_config.update(
                {
                    CONF_GROUP_TYPE: GroupType.DOMAIN,
                },
            )
            return await self.async_handle_group_creation()

        return self.async_show_form(
            step_id=Steps.DOMAIN_GROUP,
            data_schema=self.fill_schema_defaults(
                SCHEMA_GROUP_DOMAIN,
                self.get_global_powercalc_config(),
            ),
            errors=errors,
        )

    async def async_step_subtract_group(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the flow for subtract group sensor."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self.name = user_input.get(CONF_NAME)
            self.sensor_config.update(user_input)
            self.sensor_config.update(
                {
                    CONF_GROUP_TYPE: GroupType.SUBTRACT,
                },
            )
            return await self.async_handle_group_creation()

        return self.async_show_form(
            step_id=Steps.SUBTRACT_GROUP,
            data_schema=self.fill_schema_defaults(
                SCHEMA_GROUP_SUBTRACT,
                self.get_global_powercalc_config(),
            ),
            errors=errors,
        )

    async def async_handle_group_creation(self) -> FlowResult:
        """Handle the group creation."""
        self.selected_sensor_type = SensorType.GROUP

        if self.sensor_config.get(CONF_CREATE_UTILITY_METERS):
            return await self.async_step_utility_meter_options()
        return self.persist_config_entry()

    async def async_step_playbook(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the flow for playbook sensor."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self.sensor_config.update({CONF_PLAYBOOK: user_input})

            playbooks = user_input.get(CONF_PLAYBOOKS)
            if playbooks is None or len(playbooks) == 0:
                errors["base"] = "playbook_mandatory"

            if not errors:
                return await self.async_step_power_advanced()

        return self.async_show_form(
            step_id=Steps.PLAYBOOK,
            data_schema=SCHEMA_POWER_PLAYBOOK,
            errors=errors,
            last_step=False,
        )

    async def async_step_wled(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the flow for WLED sensor."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self.sensor_config.update({CONF_WLED: user_input})
            try:
                await self.validate_strategy_config()
            except SchemaFlowError as exc:
                errors["base"] = str(exc)
            if not errors:
                return cast(ConfigFlowResult, await self.async_step_power_advanced())

        return self.async_show_form(
            step_id=Steps.WLED,
            data_schema=SCHEMA_POWER_WLED,
            errors=errors,
            last_step=False,
        )

    async def async_step_library_multi_profile(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """This step gets executed when multiple profiles are found for the source entity."""
        if user_input is not None:
            selected_model: str = user_input.get(CONF_MODEL)  # type: ignore
            selected_profile = self.discovered_profiles.get(selected_model)
            if selected_profile is None:  # pragma: no cover
                return self.async_abort(reason="invalid_profile")
            self.selected_profile = selected_profile
            self.sensor_config.update(
                {
                    CONF_MANUFACTURER: selected_profile.manufacturer,
                    CONF_MODEL: selected_profile.model,
                },
            )
            return await self.async_step_post_library(user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_MODEL): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=profile.unique_id,
                                label=profile.model,
                            )
                            for profile in self.discovered_profiles.values()
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    ),
                ),
            },
        )

        manufacturer = str(self.sensor_config.get(CONF_MANUFACTURER))
        model = str(self.sensor_config.get(CONF_MODEL))
        return self.async_show_form(
            step_id=Steps.LIBRARY_MULTI_PROFILE,
            data_schema=schema,
            description_placeholders={
                "library_link": f"{LIBRARY_URL}/?manufacturer={manufacturer}",
                "manufacturer": manufacturer,
                "model": model,
            },
            last_step=False,
        )

    async def async_step_library(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Try to autodiscover manufacturer/model first.
        Ask the user to confirm this or forward to manual library selection.
        """
        if user_input is not None:
            if user_input.get(CONF_CONFIRM_AUTODISCOVERED_MODEL) and self.selected_profile:
                self.sensor_config.update(
                    {
                        CONF_MANUFACTURER: self.selected_profile.manufacturer,
                        CONF_MODEL: self.selected_profile.model,
                    },
                )
                return await self.async_step_post_library(user_input)

            return await self.async_step_manufacturer()

        if self.source_entity and self.source_entity.entity_entry and self.selected_profile is None:
            self.selected_profile = await get_power_profile_by_source_entity(self.hass, self.source_entity)
        if self.selected_profile:
            remarks = self.selected_profile.config_flow_discovery_remarks
            if remarks:
                remarks = "\n\n" + remarks
            return self.async_show_form(
                step_id=Steps.LIBRARY,
                description_placeholders={
                    "remarks": remarks,  # type: ignore
                    "manufacturer": self.selected_profile.manufacturer,
                    "model": self.selected_profile.model,
                },
                data_schema=SCHEMA_POWER_AUTODISCOVERED,
                errors={},
                last_step=False,
            )

        return await self.async_step_manufacturer()

    async def async_step_real_power(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the flow for real power sensor"""

        self.selected_sensor_type = SensorType.REAL_POWER
        if user_input is not None:
            self.name = user_input.get(CONF_NAME)
            self.sensor_config.update(user_input)
            if self.sensor_config.get(CONF_CREATE_UTILITY_METERS):
                return await self.async_step_utility_meter_options()
            return self.persist_config_entry()

        return self.async_show_form(
            step_id=Steps.REAL_POWER,
            data_schema=SCHEMA_REAL_POWER,
            errors={},
            last_step=False,
        )

    @callback
    def persist_config_entry(self) -> FlowResult:
        """Create the config entry."""
        self.sensor_config.update({CONF_SENSOR_TYPE: self.selected_sensor_type})
        self.sensor_config.update({CONF_NAME: self.name})
        if self.source_entity_id:
            self.sensor_config.update({CONF_ENTITY_ID: self.source_entity_id})

        return self.async_create_entry(title=str(self.name), data=self.sensor_config)


class PowercalcOptionsFlow(PowercalcCommonFlow, OptionsFlow):
    """Handle an option flow for PowerCalc."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        super().__init__()
        self.config_entry = config_entry
        self.sensor_config = dict(config_entry.data)
        self.sensor_type: SensorType = self.sensor_config.get(CONF_SENSOR_TYPE) or SensorType.VIRTUAL_POWER
        self.source_entity_id: str = self.sensor_config.get(CONF_ENTITY_ID)  # type: ignore
        self.strategy: CalculationStrategy | None = self.sensor_config.get(CONF_MODE)

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle options flow."""
        if self.config_entry.unique_id == ENTRY_GLOBAL_CONFIG_UNIQUE_ID:
            self.global_config = self.get_global_powercalc_config()
            return self.async_show_menu(step_id=Steps.INIT, menu_options=self.build_global_config_menu())

        self.sensor_config = dict(self.config_entry.data)
        if self.source_entity_id:
            self.source_entity = await create_source_entity(
                self.source_entity_id,
                self.hass,
            )
            result = await self.initialize_library_profile()
            if result:
                return result

        return self.async_show_menu(step_id=Steps.INIT, menu_options=self.build_menu())

    async def initialize_library_profile(self) -> FlowResult | None:
        """Initialize the library profile, when manufacturer and model are set."""
        manufacturer: str | None = self.sensor_config.get(CONF_MANUFACTURER)
        model: str | None = self.sensor_config.get(CONF_MODEL)
        if not manufacturer or not model:
            return None

        try:
            model_info = ModelInfo(manufacturer, model)
            self.selected_profile = await get_power_profile(
                self.hass,
                {},
                model_info,
            )
            if self.selected_profile and not self.strategy:
                self.strategy = self.selected_profile.calculation_strategy
        except ModelNotSupportedError:
            return self.async_abort(reason="model_not_supported")
        return None

    def build_global_config_menu(self) -> dict[Steps, str]:
        """Build menu for global configuration"""
        menu = {
            Steps.GLOBAL_CONFIGURATION: "Basic options",
        }
        if self.global_config.get(CONF_CREATE_ENERGY_SENSORS):
            menu[Steps.GLOBAL_CONFIGURATION_ENERGY] = "Energy options"
        if self.global_config.get(CONF_CREATE_UTILITY_METERS):
            menu[Steps.GLOBAL_CONFIGURATION_UTILITY_METER] = "Utility meter options"
        return menu

    def build_menu(self) -> dict[Steps, str]:
        """Build the options menu."""
        menu = {
            Steps.BASIC_OPTIONS: "Basic options",
        }
        if self.sensor_type == SensorType.VIRTUAL_POWER:
            if self.strategy and self.strategy != CalculationStrategy.LUT:
                strategy_step = STRATEGY_STEP_MAPPING[self.strategy]
                menu[strategy_step] = MENU_OPTIONS[strategy_step]
            if self.selected_profile:
                menu[Steps.LIBRARY_OPTIONS] = "Library options"
            menu[Steps.ADVANCED_OPTIONS] = "Advanced options"
        if self.sensor_type == SensorType.DAILY_ENERGY:
            menu[Steps.DAILY_ENERGY] = "Daily energy options"
        if self.sensor_type == SensorType.REAL_POWER:
            menu[Steps.REAL_POWER] = "Real power options"
        if self.sensor_type == SensorType.GROUP:
            group_type = self.sensor_config.get(CONF_GROUP_TYPE, GroupType.CUSTOM)
            if group_type == GroupType.CUSTOM:
                menu[Steps.GROUP] = "Group options"
            if group_type == GroupType.SUBTRACT:
                menu[Steps.SUBTRACT_GROUP] = "Group options"

        if self.sensor_config.get(CONF_CREATE_UTILITY_METERS):
            menu[Steps.UTILITY_METER_OPTIONS] = "Utility meter options"

        return menu

    async def async_step_global_configuration(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the global configuration step."""

        if user_input is not None:
            self.global_config.update(user_input)
            return self.persist_config_entry()

        return self.async_show_form(
            step_id=Steps.GLOBAL_CONFIGURATION,
            data_schema=self.fill_schema_defaults(
                SCHEMA_GLOBAL_CONFIGURATION,
                self.global_config,
            ),
            errors={},
        )

    async def async_step_basic_options(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the basic options flow."""
        schema = self.fill_schema_defaults(
            self.build_basic_options_schema(),
            self.sensor_config,
        )
        return await self.async_handle_options_step(user_input, schema, Steps.BASIC_OPTIONS)

    async def async_step_advanced_options(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the basic options flow."""
        schema = self.fill_schema_defaults(
            SCHEMA_POWER_ADVANCED,
            self.sensor_config,
        )
        return await self.async_handle_options_step(user_input, schema, Steps.ADVANCED_OPTIONS)

    async def async_step_utility_meter_options(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the basic options flow."""
        schema = self.fill_schema_defaults(
            SCHEMA_UTILITY_METER_OPTIONS,
            self.sensor_config,
        )
        return await self.async_handle_options_step(user_input, schema, Steps.UTILITY_METER_OPTIONS)

    async def async_step_daily_energy(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the daily energy options flow."""
        schema = self.fill_schema_defaults(
            SCHEMA_DAILY_ENERGY_OPTIONS,
            self.sensor_config[CONF_DAILY_FIXED_ENERGY],
        )
        return await self.async_handle_options_step(user_input, schema, Steps.DAILY_ENERGY)

    async def async_step_real_power(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the real power options flow."""
        schema = self.fill_schema_defaults(
            SCHEMA_REAL_POWER_OPTIONS,
            self.sensor_config,
        )
        return await self.async_handle_options_step(user_input, schema, Steps.REAL_POWER)

    async def async_step_group(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the group options flow."""
        schema = self.fill_schema_defaults(
            self.create_schema_group(self.config_entry, True),
            self.sensor_config,
        )
        return await self.async_handle_options_step(user_input, schema, Steps.GROUP)

    async def async_step_subtract_group(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the group options flow."""
        schema = self.fill_schema_defaults(
            SCHEMA_GROUP_SUBTRACT_OPTIONS,
            self.sensor_config,
        )
        return await self.async_handle_options_step(user_input, schema, Steps.SUBTRACT_GROUP)

    async def async_step_fixed(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the basic options flow."""
        return await self.async_handle_strategy_options_step(user_input)

    async def async_step_linear(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the basic options flow."""
        return await self.async_handle_strategy_options_step(user_input)

    async def async_step_wled(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the basic options flow."""
        return await self.async_handle_strategy_options_step(user_input)

    async def async_step_multi_switch(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the basic options flow."""
        return await self.async_handle_strategy_options_step(user_input)

    async def async_step_playbook(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the basic options flow."""
        return await self.async_handle_strategy_options_step(user_input)

    async def async_step_library_options(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the basic options flow."""
        self.is_library_flow = True
        if user_input is not None:
            return await self.async_step_manufacturer()

        return self.async_show_form(
            step_id=Steps.LIBRARY_OPTIONS,
            description_placeholders={
                "manufacturer": self.selected_profile.manufacturer,  # type: ignore
                "model": self.selected_profile.model,  # type: ignore
            },
            last_step=False,
        )

    async def async_handle_strategy_options_step(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the option processing for the selected strategy."""
        if not self.strategy:
            return self.async_abort(reason="no_strategy_selected")  # pragma: no cover

        step = STRATEGY_STEP_MAPPING.get(self.strategy, Steps.FIXED)

        schema = self.create_strategy_schema(self.strategy, self.source_entity_id)
        if self.selected_profile and self.selected_profile.device_type == DeviceType.SMART_SWITCH:
            schema = SCHEMA_POWER_SMART_SWITCH

        strategy_options = self.sensor_config.get(str(self.strategy)) or {}
        merged_options = {
            **self.sensor_config,
            **{k: v for k, v in strategy_options.items() if k not in self.sensor_config},
        }
        schema = self.fill_schema_defaults(schema, merged_options)
        return await self.async_handle_options_step(user_input, schema, step)

    async def async_handle_options_step(self, user_input: dict[str, Any] | None, schema: vol.Schema, step: Steps) -> FlowResult:
        """
        Generic handler for all the option steps.
        processes user input against the select schema.
        And finally persist the changes on the config entry
        """
        errors: dict[str, str] | None = {}
        if user_input is not None:
            errors = await self.process_all_options(user_input, schema)
            if not errors:
                return self.persist_config_entry()
        return self.async_show_form(step_id=step, data_schema=schema, errors=errors)

    def persist_config_entry(self) -> FlowResult:
        """Persist changed options on the config entry."""
        data = (self.config_entry.unique_id == ENTRY_GLOBAL_CONFIG_UNIQUE_ID and self.global_config) or self.sensor_config

        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data=data,
        )
        return self.async_create_entry(title="", data={})

    async def process_all_options(self, user_input: dict[str, Any], schema: vol.Schema) -> dict[str, str] | None:
        """
        Process the provided user input against the schema,
        and save the options data in current_config to save later on
        """

        assert self.cur_step is not None
        current_step: Steps = Steps(str(self.cur_step["step_id"]))
        is_strategy_step = current_step in STRATEGY_STEP_MAPPING.values()
        if self.strategy and is_strategy_step:
            if self.selected_profile and self.selected_profile.device_type == DeviceType.SMART_SWITCH:
                self._process_user_input(user_input, SCHEMA_POWER_SMART_SWITCH)
                user_input = self.get_fixed_power_config_for_smart_switch(user_input)

            strategy_options = self.build_strategy_config(
                self.strategy,
                self.source_entity_id,
                user_input or {},
            )

            if self.strategy != CalculationStrategy.LUT:
                self.sensor_config.update({str(self.strategy): strategy_options})

            try:
                await self.validate_strategy_config()
            except SchemaFlowError as exc:
                return {"base": str(exc)}

        self._process_user_input(user_input, schema)

        if self.sensor_type == SensorType.DAILY_ENERGY and current_step == Steps.DAILY_ENERGY:
            self.sensor_config.update(self.build_daily_energy_config(user_input, SCHEMA_DAILY_ENERGY_OPTIONS))

        if CONF_ENTITY_ID in user_input:
            self.sensor_config[CONF_ENTITY_ID] = user_input[CONF_ENTITY_ID]

        return None

    def _process_user_input(
        self,
        user_input: dict[str, Any],
        schema: vol.Schema,
    ) -> None:
        """
        Process the provided user input against the schema.
        Update the current_config dictionary with the new options. We use that to save the data to config entry later on.
        """
        for key in schema.schema:
            if isinstance(key, vol.Marker):
                key = key.schema
            if key in user_input:
                self.sensor_config[key] = user_input.get(key)
            elif key in self.sensor_config:
                self.sensor_config.pop(key)

    def build_basic_options_schema(self) -> vol.Schema:
        """Build the basic options schema. depending on the selected sensor type."""
        if self.sensor_type == SensorType.REAL_POWER:
            return SCHEMA_UTILITY_METER_TOGGLE

        if self.sensor_type == SensorType.DAILY_ENERGY:
            return SCHEMA_UTILITY_METER_TOGGLE

        if self.sensor_type == SensorType.GROUP:
            return vol.Schema(
                {
                    **SCHEMA_ENERGY_SENSOR_TOGGLE.schema,
                    **SCHEMA_UTILITY_METER_TOGGLE.schema,
                },
            )

        return vol.Schema(  # type: ignore
            {
                vol.Optional(CONF_ENTITY_ID): self.create_source_entity_selector(),
            },
        ).extend(SCHEMA_POWER_OPTIONS.schema)
