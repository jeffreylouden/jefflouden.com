#!/usr/bin/env bash

function createlog {
    last_day="1983-01-07"
    day=`date +"%Y-%m-%d"`

    dots=""

    while ! [[ $day < $last_day ]]; do
        tooltip=""
        active=""
        if test -f "log/$day"; then
            tooltip="<tooltip>`cat log/$day`<\/tooltip>"
            active=' class="active"'
        fi
        dots+="<dot$active>.$tooltip<\/dot>\n"

        day=$(date -j -v-1d -f %Y-%m-%d $day +%Y-%m-%d)
    done

    cp template.html index.html
    sed -i '' "s/%s/$dots/" index.html
}

echo -e "🏗 building..."

createlog

echo -e "\033[32m🎉 build complete.\033[0m"
exit