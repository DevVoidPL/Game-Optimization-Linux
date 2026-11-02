from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import pytest

from game_optimization_linux.config import QML_DIR
from test_couch_details import GAME, _call, _load, _plain, _settle
from test_couch_feature_parity import Controller, choose


@pytest.fixture
def opened_editor():
    opened = []

    def open_editor(name, controller=None):
        controller = controller or Controller()
        view = QQuickView()
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.setInitialProperties({'controller': controller,
            'gameData': {'id': 'first', 'launcher': 'Heroic'}})
        view.setSource(QUrl.fromLocalFile(str(QML_DIR / 'couch' / name)))
        assert view.rootObject(), view.errors()
        view.resize(1100, 850)
        view.show()
        QTest.qWait(80)
        root = view.rootObject()
        _call(root, 'handlePadAction', 'Focus')
        opened.append((view, controller))
        return view, root, controller

    yield open_editor
    for view, _ in reversed(opened):
        view.close()
        view.deleteLater()
    QTest.qWait(20)


@pytest.mark.parametrize('name', ['CouchFrameGenerationEditor.qml',
    'CouchPostProcessingEditor.qml', 'CouchGpuMemoryEditor.qml'])
def test_polling_keeps_delegate_and_focus_stable(opened_editor, name):
    view, root, controller = opened_editor(name)
    _call(root, 'handlePadAction', 'NavigateDown')
    _call(root, 'handlePadAction', 'NavigateDown')
    before = view.activeFocusItem()
    assert before
    name_before = before.objectName()
    QTest.qWait(3300)
    assert view.activeFocusItem() is before, (name_before, view.activeFocusItem())
    assert before.objectName() == name_before
    assert not controller.calls


def test_option_rows_have_loaded_icons(opened_editor):
    view, root, _ = opened_editor('CouchPostProcessingEditor.qml')
    option = view.activeFocusItem()
    assert option.property('iconSource').toString()
    icon = option.findChild(QObject, 'couchTileIcon')
    assert icon and icon.property('imageStatus') == 1


def test_frame_generation_can_select_backend_when_executable_missing(opened_editor):
    controller = Controller()
    controller.fg = {'success': False, 'operationError': 'Choose the main game executable',
                     'defaults': controller.fg['defaults']}
    _, root, _ = opened_editor('CouchFrameGenerationEditor.qml', controller)
    choose(root, 'backend', 'NavigateRight')
    assert root.findChild(QObject, 'couchFrameGenerationState').property('backend') == 'lsfg'


@pytest.mark.parametrize('name', ['CouchFrameGenerationEditor.qml',
    'CouchPostProcessingEditor.qml', 'CouchGpuMemoryEditor.qml'])
def test_refresh_action_is_callable(opened_editor, name):
    _, root, _ = opened_editor(name)
    choose(root, 'refresh')
    assert not root.property('confirmation')


def test_frame_generation_has_controller_executable_selection(opened_editor):
    _, root, _ = opened_editor('CouchFrameGenerationEditor.qml')
    entries = _call(root, 'rows')
    assert any(row['id'] == 'executable' for row in entries)


class ExecutableController(Controller):
    @Slot(str, result='QVariantMap')
    def getOptiScalerStatus(self, key):
        return {'success': True, 'executableCandidates': [
            {'relativePath': 'Binaries/Win64/Game.exe', 'score': 90},
            {'relativePath': 'Launcher.exe', 'score': 10}]}

    @Slot(str, str, result='QVariantMap')
    def rememberOptiScalerExecutable(self, key, path):
        self.calls.append(('executable', key, path))
        return {'success': True}

    @Slot(str, str, str, str, str, bool, 'QVariantMap', result=bool)
    def startOptiScalerInstall(self, key, executable, dll, operation, digest, online, options):
        self.calls.append(('install', key, executable, dll, operation, digest, online, options))
        return True


def test_executable_picker_selects_only_after_confirm(opened_editor):
    controller = ExecutableController()
    _, root, _ = opened_editor('CouchFrameGenerationEditor.qml', controller)
    choose(root, 'executable')
    assert not controller.calls
    _call(root, 'handlePadAction', 'Confirm')
    assert controller.calls == [('executable', 'first', 'Binaries/Win64/Game.exe')]


def test_optiscaler_offers_executable_picker_before_install():
    controller = ExecutableController()
    engine, root = _load(QML_DIR / 'couch/CouchGameDetails.qml', {
        'controller': controller, 'game': GAME, 'width': 1920, 'height': 1080})
    try:
        root.setProperty('optiScalerData', {'success': True, 'installationState': 'not_installed',
            'executableStatus': 'ambiguous', 'executableCandidates': [{'relativePath': 'Game.exe'}]})
        root.setProperty('selectedTab', 3)
        entries = _plain(root.property('actionModel'))
        assert any(row['id'] == 'optiscaler-executable' and row['enabled'] for row in entries)
    finally:
        root.deleteLater()
        _settle()


@pytest.mark.parametrize('cancel', [True, False])
def test_optiscaler_install_requires_explicit_pad_confirmation(cancel):
    controller = ExecutableController()
    engine, root = _load(QML_DIR / 'couch/CouchGameDetails.qml', {
        'controller': controller, 'game': GAME, 'width': 1920, 'height': 1080})
    try:
        root.setProperty('optiScalerData', {'success': True, 'installed': False,
            'executable': 'Binaries/Win64/Game.exe', 'installationState': 'not_installed'})
        root.setProperty('selectedTab', 3)
        root.setProperty('focusArea', 2)
        entries = _plain(root.property('actionModel'))
        root.setProperty('selectedAction', next(i for i, row in enumerate(entries)
                                               if row['id'] == 'optiscaler-install'))
        _call(root, 'handleAction', 'Confirm')
        assert root.property('confirmationOpen') and not controller.calls
        _call(root, 'handleAction', 'NavigateRight')
        _call(root, 'handleAction', 'Back' if cancel else 'Confirm')
        assert not root.property('confirmationOpen')
        if cancel:
            assert not controller.calls
        else:
            assert controller.calls == [('install', 'g1', 'Binaries/Win64/Game.exe',
                                          'auto', 'install', '', True, {})]
    finally:
        root.deleteLater()
        _settle()


def test_switching_game_closes_optiscaler_confirmation():
    controller = ExecutableController()
    engine, root = _load(QML_DIR / 'couch/CouchGameDetails.qml', {
        'controller': controller, 'game': GAME, 'width': 1920, 'height': 1080})
    try:
        _call(root, 'openOptiScalerConfirmation', 'install')
        root.setProperty('game', dict(GAME, id='second'))
        _settle()
        assert not root.property('confirmationOpen')
        assert not controller.calls
    finally:
        root.deleteLater()
        _settle()


@pytest.mark.parametrize('name', ['CouchFrameGenerationEditor.qml', 'CouchPostProcessingEditor.qml'])
def test_picker_cancel_and_game_switch_do_not_save(opened_editor, name):
    controller = ExecutableController()
    view, root, _ = opened_editor(name, controller)
    choose(root, 'executable')
    picker = root.findChild(QObject, 'couchExecutablePicker')
    assert picker.property('opened')
    _call(root, 'handlePadAction', 'Back')
    assert not picker.property('opened')
    assert view.activeFocusItem().objectName() == 'couchOption-executable'
    choose(root, 'executable')
    root.setProperty('gameData', {'id': 'second', 'launcher': 'Heroic'})
    _settle()
    assert not picker.property('opened')
    assert not controller.calls


def test_picker_reports_rejected_path_without_closing(opened_editor):
    class RejectingController(ExecutableController):
        @Slot(str, str, result='QVariantMap')
        def rememberOptiScalerExecutable(self, key, path):
            return {'success': False, 'error': 'Executable must be inside the game directory'}

    _, root, _ = opened_editor('CouchFrameGenerationEditor.qml', RejectingController())
    choose(root, 'executable')
    _call(root, 'handlePadAction', 'Confirm')
    picker = root.findChild(QObject, 'couchExecutablePicker')
    assert picker.property('opened')
    assert 'inside the game directory' in picker.property('error')


def test_details_picker_is_a_page_modal():
    controller = ExecutableController()
    engine, root = _load(QML_DIR / 'couch/CouchMain.qml', {
        'controller': controller, 'width': 1920, 'height': 1080})
    try:
        root.setProperty('section', 'details')
        details = root.findChild(QObject, 'couchGameDetails')
        details.setProperty('game', GAME)
        details.setProperty('selectedTab', 3)
        details.setProperty('optiScalerData', {'success': True})
        entries = _plain(details.property('actionModel'))
        details.setProperty('selectedAction', next(i for i, row in enumerate(entries)
                                                  if row['id'] == 'optiscaler-executable'))
        assert _call(details, 'activateAction') == 'open'
        assert root.property('pageModalOpen')
        _call(root, 'closePageModal')
        assert not details.findChild(QObject, 'couchExecutablePicker').property('opened')
    finally:
        root.deleteLater()
        _settle()


def test_details_metadata_refresh_keeps_tab_and_action():
    controller = ExecutableController()
    engine, root = _load(QML_DIR / 'couch/CouchGameDetails.qml', {
        'controller': controller, 'game': GAME, 'width': 1920, 'height': 1080})
    try:
        root.setProperty('selectedTab', 2)
        root.setProperty('focusArea', 2)
        root.setProperty('selectedAction', 3)
        root.setProperty('game', dict(GAME, name='Updated artwork'))
        _settle()
        assert root.property('focusArea') == 2
        assert root.property('selectedAction') == 3
    finally:
        root.deleteLater()
        _settle()


def test_optiscaler_confirmation_is_controller_operable():
    controller = ExecutableController()
    engine, root = _load(QML_DIR / 'couch/CouchGameDetails.qml', {
        'controller': controller, 'game': GAME, 'width': 1920, 'height': 1080})
    try:
        _call(root, 'openOptiScalerConfirmation', 'install')
        assert root.property('confirmationChoice') == 0
        _call(root, 'handleAction', 'NavigateRight')
        assert root.property('confirmationChoice') == 1
        _call(root, 'handleAction', 'Back')
        assert not root.property('confirmationOpen')
        assert not controller.calls
    finally:
        root.deleteLater()
        _settle()