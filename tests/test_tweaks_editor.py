"""Draft persistence, selected-runner evidence and reversible launch overrides."""
from dataclasses import replace
import json
from pathlib import Path
import struct

import pytest

from game_optimization_linux.models import GameOptimizationProfile
from game_optimization_linux.models.proton_tweaks import ProtonTweaksProfile
from game_optimization_linux.providers.demo import demo_games
from game_optimization_linux.services.proton_tweaks import ProtonTweaksRepository, ProtonTweaksError
from game_optimization_linux.services.tweaks_capabilities import conflicts, runner_capabilities, log_directory
from game_optimization_linux.services.optimization_runtime import OptimizationLaunchPlanner, RuntimeToolAvailability


def test_migrates_boolean_profile_and_preserves_unknown_fields(tmp_path):
    repo=ProtonTweaksRepository(tmp_path)
    path=repo.path('42'); path.parent.mkdir()
    path.write_text(json.dumps({'schema_version':1,'app_id':'42','enabled_tweaks':['no_fsync'], 'custom_metadata':{'keep':True}}))
    assert repo.load('42').states=={'no_fsync':'enabled'}
    draft=repo.from_payload('42', {'states':{'no_fsync':'default','proton_log':'disabled'}})
    assert draft.environment()=={'PROTON_LOG':'0'}
    assert repo.load('42').environment()=={'PROTON_NO_FSYNC':'1'}
    repo.save(draft)
    assert repo.load('42').extra['custom_metadata']=={'keep':True}
    assert repo.load('43').environment()=={}
    repo.save(repo.from_payload('42',{'states':{}}))
    assert repo.load('42').environment()=={}


@pytest.mark.parametrize('states', [{'unknown':'enabled'},{'proton_log':'garbage'},{'disable_hdr':'disabled'}])
def test_rejects_arbitrary_states_and_nonexistent_negative_values(tmp_path, states):
    with pytest.raises(ProtonTweaksError):
        ProtonTweaksRepository(tmp_path).from_payload('42',{'states':states})


def _proton(home, app='42', script='PROTON_ENABLE_WAYLAND PROTON_NO_NTSYNC LOW_LATENCY_LAYER'):
    root=home/'.local/share/Steam'; (root/'config').mkdir(parents=True)
    (root/'config/config.vdf').write_text('"CompatToolMapping" {"'+app+'" {"name" "test-proton"}}')
    tool=root/'compatibilitytools.d/test-proton'; tool.mkdir(parents=True)
    (tool/'proton').write_text(script)
    return tool


def test_support_uses_selected_runner_and_real_components_not_name(tmp_path):
    tool=_proton(tmp_path)
    game=replace(demo_games()[0],steam_app_id='42')
    caps=runner_capabilities(game,tmp_path)
    assert caps['support']['no_ntsync']=='present'
    assert caps['support']['prefer_sdl']=='unsupported'
    assert caps['support']['wayland']=='unsupported'  # script alone is insufficient
    assert caps['support']['low_latency']=='unsupported'
    driver=tool/'files/lib/wine/x86_64-windows/winewayland.drv'; driver.parent.mkdir(parents=True); driver.touch()
    assert runner_capabilities(game,tmp_path)['support']['wayland']=='present'
    # An installed but unselected runner is never promoted to selected.
    assert runner_capabilities(replace(game,steam_app_id='99'),tmp_path)['selection']=='unknown'


def test_layer_architecture_and_binary_are_required(tmp_path):
    tool=_proton(tmp_path)
    manifest=tool/'files/share/low_latency_layer/implicit_layer.d/layer.json'
    manifest.parent.mkdir(parents=True); manifest.write_text(json.dumps({'layer':{'library_path':'libVkLayer_KORTHOS_LowLatency.so','enable_environment':{'LOW_LATENCY_LAYER':'1'},'disable_environment':{'DISABLE_LOW_LATENCY_LAYER':'1'}}}))
    lib=tool/'files/lib/x86_64-linux-gnu/libVkLayer_KORTHOS_LowLatency.so'
    lib.parent.mkdir(parents=True); lib.write_bytes(b'\x7fELF\x02LOW_LATENCY_LAYER_REFLEX')
    exe=tmp_path/'game.exe'; pe=bytearray(256); pe[:2]=b'MZ'; struct.pack_into('<I',pe,60,128); pe[128:132]=b'PE\0\0'; struct.pack_into('<H',pe,132,0x14c); exe.write_bytes(pe)
    game=replace(demo_games()[0],steam_app_id='42',executable_path=str(exe))
    assert runner_capabilities(game,tmp_path)['support']['low_latency']=='unsupported'
    (tool/'proton').write_text((tool/'proton').read_text()+' PROTON_USE_WOW64')
    assert runner_capabilities(game,tmp_path)['support']['low_latency']=='unknown'
    struct.pack_into('<H',pe,132,0x8664);exe.write_bytes(pe)
    caps=runner_capabilities(game,tmp_path)
    assert caps['support']['low_latency']=='present'
    assert caps['support']['latency_reflex']=='present'


@pytest.mark.parametrize('states,kwargs,reason', [
    ({'wayland':'enabled'},{'gamescope':True},'wayland_gamescope'),
    ({'latency_reflex':'enabled'},{},'reflex_requires_layer'),
    ({'low_latency':'enabled','use_wined3d':'enabled'},{},'layer_requires_vulkan'),
    ({'low_latency':'enabled','latency_reflex':'enabled','disable_nvapi':'enabled'},{},'reflex_requires_nvapi'),
    ({'low_latency':'enabled'},{'frame_generation':True},'latency_stack'),
])
def test_concrete_conflicts(states,kwargs,reason):
    assert reason in conflicts(states,{},**kwargs)


def test_disabled_layer_uses_real_disable_switch_and_preserves_user_veto():
    profile=ProtonTweaksProfile(1,'42',states={'low_latency':'disabled'})
    assert profile.environment()=={'LOW_LATENCY_LAYER':'0','DISABLE_LOW_LATENCY_LAYER':'1'}
    assert 'external_layer_veto' in conflicts({'low_latency':'enabled'},{'DISABLE_LOW_LATENCY_LAYER':'1'})
    assert 'DISABLE_LOW_LATENCY_LAYER' not in ProtonTweaksProfile(1,'42',states={'low_latency':'enabled'}).environment()


def test_planner_merges_tokens_and_default_leaves_foreign_settings():
    profile=GameOptimizationProfile.default('42')
    absent=RuntimeToolAvailability('none',False,'','')
    inherited={'DXIL_SPIRV_CONFIG':'foo;bar','CUSTOM':'keep'}
    plan=OptimizationLaunchPlanner().build(profile,['/usr/bin/true'],gamemode=absent,gamescope=absent,existing_environment=inherited,proton_environment={'DXIL_SPIRV_CONFIG':'wmma_rdna3_workaround'})
    assert plan.environment['DXIL_SPIRV_CONFIG']=='foo,bar,wmma_rdna3_workaround'
    assert 'CUSTOM' not in plan.environment
    default=OptimizationLaunchPlanner().build(profile,['/usr/bin/true'],gamemode=absent,gamescope=absent,existing_environment=inherited,proton_environment={})
    assert 'DXIL_SPIRV_CONFIG' not in default.environment


def test_logs_use_launchers_own_home_and_are_opt_in(tmp_path):
    repo=ProtonTweaksRepository(tmp_path/'games')
    game=replace(demo_games()[0],launcher_variant='flatpak')
    path=log_directory(game,'42',tmp_path)
    assert '.var/app/com.valvesoftware.Steam/data/' in str(path)
    assert not path.exists()
    draft=repo.from_payload('42',{'states':{'proton_log':'enabled'}})
    repo.save(replace(draft,extra={'log_directory':str(path)}))
    assert repo.environment('42')['PROTON_LOG_DIR']==str(path)
    repo.save(repo.from_payload('42',{'states':{}}))
    assert repo.environment('42')=={}

@pytest.mark.parametrize('launcher_name', ['Heroic','Lutris'])
@pytest.mark.parametrize('variant', ['native','flatpak'])
def test_launcher_apply_default_and_restore_preserve_foreign_values(tmp_path, launcher_name, variant):
    import yaml
    import sqlite3
    from game_optimization_linux.models import Launcher
    from game_optimization_linux.services.launcher_integration import LauncherIntegrationService, LauncherPlan
    from game_optimization_linux.services.tweaks_capabilities import merge_tweak_environment
    launcher=Launcher(launcher_name)
    key='game-42'
    if launcher is Launcher.HEROIC:
        base=tmp_path/('.var/app/com.heroicgameslauncher.hgl/config/heroic' if variant=='flatpak' else '.config/heroic')/'GamesConfig'
        path=base/(key+'.json'); base.mkdir(parents=True)
        original={key:{'wineVersion':{'type':'proton'},'enviromentOptions':[{'key':'KEEP','value':'yes'},{'key':'DXIL_SPIRV_CONFIG','value':'foo;bar'}],'wrapperOptions':[]}}
        path.write_text(json.dumps(original)); read=lambda:json.loads(path.read_text())[key]['enviromentOptions']
        environment=lambda:{x['key']:x['value'] for x in read()}
        game=replace(demo_games()[0],id='heroic-legendary-'+key,launcher=launcher,steam_app_id=None,launcher_game_id=key,launcher_variant=variant)
    else:
        base=tmp_path/('.var/app/net.lutris.Lutris/config/lutris' if variant=='flatpak' else '.config/lutris')/'games';base.mkdir(parents=True)
        path=base/(key+'.yml'); original={'runner':'wine','game':{'exe':'/game.exe'},'wine':{'version':'GE-Proton'},'system':{'env':{'KEEP':'yes','DXIL_SPIRV_CONFIG':'foo;bar'}}};path.write_text(yaml.safe_dump(original))
        environment=lambda:yaml.safe_load(path.read_text())['system']['env']
        data=tmp_path/('.var/app/net.lutris.Lutris/data/lutris' if variant=='flatpak' else '.local/share/lutris');data.mkdir(parents=True)
        connection=sqlite3.connect(data/'pga.db');connection.execute('CREATE TABLE games (id INTEGER PRIMARY KEY, configpath TEXT)');connection.execute('INSERT INTO games VALUES (42, ?)',(key,));connection.commit();connection.close()
        game=replace(demo_games()[0],id='lutris-42',launcher=launcher,steam_app_id=None,launcher_game_id='42',launcher_variant=variant)
    before=path.read_bytes()
    wanted=[{'PROTON_LOG':'1','PROTON_NO_NTSYNC':'0','DXIL_SPIRV_CONFIG':'wmma_rdna3_workaround'}]
    def plan(_game,**kwargs):
        return LauncherPlan(environment=merge_tweak_environment(environment(),wanted[0]))
    service=LauncherIntegrationService(tmp_path/'state',plan,home=tmp_path,process_commands=lambda:())
    service.apply(game)
    env=environment(); assert env['KEEP']=='yes'; assert env['PROTON_NO_NTSYNC']=='0';assert env['DXIL_SPIRV_CONFIG']=='foo,bar,wmma_rdna3_workaround'
    wanted[0]={}
    assert service.status(game)["canApply"]
    service.apply(game)
    assert environment()=={'KEEP':'yes','DXIL_SPIRV_CONFIG':'foo;bar'}
    assert path.read_bytes()==before


def test_unknown_state_shape_is_rejected(tmp_path):
    with pytest.raises(ProtonTweaksError):
        ProtonTweaksRepository(tmp_path).from_payload('42',{'states':[]})


def test_controller_preview_is_read_only_and_blocks_conflicting_apply(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from game_optimization_linux.controllers.optimization_controller import OptimizationController
    from game_optimization_linux.services import GameOptimizationProfileRepository
    import game_optimization_linux.controllers.optimization_controller as module
    repo=ProtonTweaksRepository(tmp_path/'games');repo.save(repo.from_payload('42',{'states':{'no_fsync':'enabled'}}))
    opt=GameOptimizationProfileRepository(tmp_path/'games');opt.save(replace(GameOptimizationProfile.default('42'),gamescope_enabled=True,gamescope_mode='native'))
    game=replace(demo_games()[0],steam_app_id='42')
    app=SimpleNamespace(_proton_tweaks_repository=repo,_optimization_profile_repository=opt,_system_info={},_resolve_game=lambda *args,**kwargs:game,_runner_integration=SimpleNamespace(steam_launch_option_status=lambda game:SimpleNamespace(raw_launch_options='KEEP=1 %command%'),steam_command=lambda key:'runner --appid '+key+' -- %command%'))
    controller=OptimizationController(app)
    monkeypatch.setattr(controller,'_launch_activation',lambda game:{'state':'active'})
    monkeypatch.setattr(module,'host_home_directory',lambda:tmp_path)
    monkeypatch.setattr(module,'runner_capabilities',lambda *args:{'support':{'wayland':'present','no_fsync':'present'}})
    before=repo.path('42').read_bytes()
    result=controller.previewProtonTweaks(game.id,{'states':{'wayland':'enabled'}})
    assert result['success'] and 'wayland_gamescope' in result['conflicts']
    assert repo.path('42').read_bytes()==before
    assert not log_directory(game,'42',tmp_path).exists()
    assert not controller.saveProtonTweaks(game.id,{'states':{'wayland':'enabled'}})['success']
    assert repo.path('42').read_bytes()==before
    result=controller.previewProtonTweaks(game.id,{'states':{}})
    assert result['changes']==[{'key':'PROTON_NO_FSYNC','before':'1','after':None}]
