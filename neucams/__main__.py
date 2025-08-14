import sys
from argparse import ArgumentParser
from PyQt5.QtWidgets import QApplication
from neucams.view.widgets import NeuCamsWindow
from neucams.view.launcher import SplashWindow
from neucams.utils import get_preferences, display

def main():
    parser = ArgumentParser(description='Labcams: multiple camera control and recording.')
    parser.add_argument('-p','--pref',metavar='preference',
                        type=str,help='Preference filename',default = None)
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    args = parser.parse_args()

    app = QApplication(sys.argv)
    if args.pref:
        ret, prefs = get_preferences(args.pref)
        if not ret:
            display('Warning: could not load preferences')
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