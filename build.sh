#!/usr/bin/env bash

function createlog {
    last_day="1983-01-07"
    day=`date +"%Y-%m-%d"`
    dots=""

    echo -e "generating log"
    while ! [[ $day < $last_day ]]; do
        if test -f "log/$day"; then
            dots+="<d title='`cat log/$day`'>+<\/d>"
        else
            dots+="-"
        fi

        if [ $(uname) = 'Darwin' ]; then
            day=$(date -j -v-1d -f %Y-%m-%d $day +%Y-%m-%d)
        elif [ $(uname) = 'Linux' ]; then
            day=$(date -I -d "$day - 1 day")
        fi
        
    done

    echo -e "copying over template"
    cp index.template index.html

    if [ $(uname) = 'Darwin' ]; then
        echo -e "[darwin] sed replacing"
        sed -i '' "s/%s/$dots/g" index.html
    elif [ $(uname) = 'Linux' ]; then
        echo -e "[linux] sed replacing"
        sed -i "s/%s/$dots/g" index.html
    fi
    
}

echo -e "🏗 building..."

createlog

echo -e "\033[32m🎉 build complete.\033[0m"
exit
