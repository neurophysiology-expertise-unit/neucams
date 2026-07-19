import sys
from argparse import ArgumentParser
from PyQt5.QtWidgets import QApplication
from neucams.view.widgets import NeuCamsWindow
from neucams.view.launcher import SplashWindow
from neucams.utils import get_preferences, display

def main():
    parser = ArgumentParser(description='NeuCams: multiple camera control and recording.')
    parser.add_argument('-p','--pref',metavar='preference',
                        type=str,help='Preference filename',default = None)
    parser.add_argument('--verbose', action='store_true', help='Enable verbose (INFO) logging')
    args = parser.parse_args()

    if args.verbose:
        # Root logger defaults to WARNING (see utils.py), which swallows every
        # display(..., 'info') status message. --verbose surfaces them.
        import logging
        logging.getLogger().setLevel(logging.INFO)

    app = QApplication(sys.argv)
    if args.pref:
        ret, prefs = get_preferences(args.pref)
        # get_preferences -> True on success, False if the file was missing (a
        # template is written), or an error string if it failed to load.
        if isinstance(ret, str):
            display(f'Could not load preferences: {ret}', level='error')
        elif ret is False:
            display('Preferences file not found; a template was created.', level='warning')
        w = NeuCamsWindow(preferences = prefs)
        w.show()
    else:
        splash = SplashWindow()
        splash.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()