"""Rendered editor and controller navigation probe, run in a fresh Qt process."""
import json
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QObject, Signal, Slot, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication, QColor
from PySide6.QtQml import QQmlExpression, QQmlEngine

from qml_runtime_probe import _view, _item, _invoke_qml, _settle, QML_ROOT
from game_optimization_linux.services.proton_tweaks import ProtonTweaksRepository
from game_optimization_linux.translations import TranslationManager


class Controller(QObject):
    protonTweaksChanged=Signal(str)
    def __init__(self,path):
        super().__init__(); self.repo=ProtonTweaksRepository(path); self.writes=0; self.launcher_applies=0
    @Slot(str,result='QVariantMap')
    def getProtonTweaks(self,key):
        value=self.repo.to_qml(self.repo.load(key))
        value.update(capabilities={'runner':'GE-Proton test fixture','boundary':'host','support':{'wayland':'present','proton_log':'present'},'components':{'lowLatencyArchitectures':['64']}},activation={'state':'active','canApply':True,'environment':{},'changes':[]},steamCommand='runner --appid '+key+' -- %command%',logDirectory='/logs/proton',runtimeState='unverified')
        return value
    @Slot(str,'QVariantMap',result='QVariantMap')
    def previewProtonTweaks(self,key,values):
        current=self.repo.load(key); draft=self.repo.from_payload(key,values)
        result=self.getProtonTweaks(key); result['environment']=draft.environment()
        old=current.environment(); new=draft.environment()
        result.update(changes=[{'key':k,'before':old.get(k),'after':new.get(k)} for k in old.keys()|new.keys() if old.get(k)!=new.get(k)],conflicts=[],unsupported=[])
        return result
    @Slot(str,result='QVariantMap')
    def applyLauncherIntegration(self,key):
        self.launcher_applies+=1
        return {'success':True}

    @Slot(str,'QVariantMap',result='QVariantMap')
    def saveProtonTweaks(self,key,values):
        self.repo.save(self.repo.from_payload(key,values)); self.writes+=1; self.protonTweaksChanged.emit(key)
        return self.getProtonTweaks(key)


def run():
    app=QGuiApplication.instance() or QGuiApplication([])
    messages=[]; qInstallMessageHandler(lambda kind,context,message: messages.append(message))
    out=Path(os.environ.get('TWEAKS_PROBE_OUTPUT','.go-tmp/tweaks/ui'));out.mkdir(parents=True,exist_ok=True)
    result={'screenshots':[]}
    with tempfile.TemporaryDirectory(prefix='gameopti-tweaks-ui-') as tmp:
        temp=Path(tmp); controller=Controller(temp/'games')
        wrapper=temp/'Probe.qml'
        wrapper.write_text('''import QtQuick
import QtQuick.Controls
import "'''+(QML_ROOT/'pages/details').as_uri()+'''" as Details
import "'''+QML_ROOT.as_uri()+'''" as App
ScrollView {
    property bool couchMode: false
    property var gameData: ({"id":"42","steamAppId":"42"})
    contentWidth: availableWidth
    Details.ProtonTweaksSection {
        objectName: "tweaksProbeEditor"
        width: parent.width
        controller: probeController
        gameData: parent.parent.gameData
        couchMode: parent.parent.couchMode
    }
}''')
        # Direct parent hierarchy is style-dependent: use an explicit root id.
        wrapper.write_text(wrapper.read_text().replace('ScrollView {','ScrollView {\n id: probe').replace('width: parent.width','width: probe.availableWidth').replace('gameData: parent.parent.gameData','gameData: probe.gameData').replace('couchMode: parent.parent.couchMode','couchMode: probe.couchMode'))
        translations=TranslationManager(app)
        for language in ('en','pl','es'):
            translations.set_language(language)
            for couch,theme,width in ((False,'dark',1100),(False,'light',480),(True,'dark',1200),(True,'light',1100)):
                view,root=_view(app,str(wrapper),width,880,context_properties={'probeController':controller},initial_properties={'couchMode':couch})
                expression=QQmlExpression(QQmlEngine.contextForObject(root),root,'App.Theme.mode="'+theme+'"');expression.evaluate(); assert not expression.hasError()
                view.setColor(QColor('#0B1018' if theme=='dark' else '#F3F6FA'))
                editor=_item(root,'tweaksProbeEditor'); _settle(app,6)
                assert editor.property('entries').toVariant()
                _invoke_qml(editor,'setState','wayland','enabled')
                _invoke_qml(editor,'load',False)
                assert editor.property('dirty')
                assert editor.property('states').toVariant()['wayland']=='enabled'
                _invoke_qml(editor,'preview'); assert editor.property('previewed')
                assert _item(editor,'applyTweaks').isEnabled()
                _invoke_qml(editor,'reset',True); assert not editor.property('states').toVariant()
                assert controller.repo.load('42').environment()=={}
                _invoke_qml(editor,'setState','proton_log','enabled'); _invoke_qml(editor,'preview'); _invoke_qml(editor,'save')
                assert controller.repo.load('42').environment()=={'PROTON_LOG':'1'}
                _invoke_qml(editor,'setState','proton_log','disabled')
                root.setProperty('gameData',{'id':'43','steamAppId':'43'});_settle(app,4)
                assert not editor.property('dirty'); assert not editor.property('states').toVariant()
                root.setProperty('gameData',{'id':'42','steamAppId':'42'});_settle(app,4)
                assert editor.property('states').toVariant()['proton_log']=='enabled'
                if couch:
                    editor.setProperty('integrationOpen',True)
                    root.setProperty('gameData',{'id':'43','launcher':'Heroic'});_settle(app,3)
                    button=_item(editor,'tweaksApplyLauncher');assert button.isEnabled() and button.isVisible()
                    previous=controller.launcher_applies
                    _invoke_qml(button,'clicked')
                    assert controller.launcher_applies==previous+1
                    root.setProperty('gameData',{'id':'42','steamAppId':'42','launcher':'Steam'});_settle(app,3)
                    assert _item(editor,'tweaksRunnerCommand').property('text')=='runner --appid 42 -- %command%'
                    view.grabWindow().save(str(out/f'couch-integration-{language}-{theme}.png'))
                    editor.setProperty('integrationOpen',False)
                editor.setProperty('categoryIndex',0)
                # Focus starts on Refresh, then integration and category controls.
                _invoke_qml(editor,'handlePadAction','Focus')
                for _ in range(6):_invoke_qml(editor,'handlePadAction','NavigateDown')
                combo=_item(editor,'tweak-wayland'); combo.forceActiveFocus()
                _invoke_qml(editor,'handlePadAction','NavigateRight')
                assert editor.property('states').toVariant()['wayland']=='enabled'
                assert combo.hasActiveFocus()
                _settle(app,3)
                for control in ('tweak-wayland','tweak-disable_hdr','tweak-prefer_sdl'):
                    item=_item(editor,control); pos=item.mapToItem(editor,0,0)
                    assert pos.x()>=0 and pos.x()+item.width() <= editor.width()+1
                name=f'{"couch" if couch else "desktop"}-{language}-{theme}-{width}.png'
                view.grabWindow().save(str(out/name));result['screenshots'].append(name)
                view.close();view.deleteLater();_settle(app,3)
                controller.repo.save(controller.repo.from_payload('42',{'states':{}}))
        result.update(dirty_refresh=True,reset_draft_only=True,apply=True,game_isolation=True,pad_combo=True,couch_launcher_action=True,messages=messages)
        assert not messages, messages
    (out/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))

if __name__=='__main__':run()
