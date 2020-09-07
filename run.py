import os

from k8sh import k8shConfigPath, shell


def main():
    configfile = k8shConfigPath()
    sh = shell.from_configfile(configfile)
    sh.cmdloop()


if __name__ == "__main__":
    main()
