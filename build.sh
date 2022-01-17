#!/usr/bin/env bash

function createlog {
    last_day="1983-01-07"
    day=`date +%Y-%m-%d`
    dots=""

    echo $day

    echo -e "generating log"
    while ! [[ $day < $last_day ]]; do
        if test -f "log/$day"; then
            dots+="<d title='`cat log/$day`'>+<\/d>"
        else
            dots+="-"
        fi

        day=$(date -d "$day -1 days" +%Y-%m-%d)
        echo $day
        
    done

    echo -e "copying over template"
    cp index.template public/index.html

    if [ $(uname) = 'Darwin' ]; then
        echo -e "[darwin] sed replacing"
        sed -i '' "s/%s/$dots/g" public/index.html
    elif [ $(uname) = 'Linux' ]; then
        echo -e "[linux] sed replacing"
        sed -i "s/%s/$dots/g" public/index.html
    fi
    
}

echo -e "🏗 building..."

createlog

echo -e "\033[32m🎉 build complete.\033[0m"
exit
